"""Tests for IdToken verification in the auth-fastapi example.

Run from examples/auth-fastapi:
    pip install -r app/requirements.txt -r tests/requirements.txt
    pytest tests
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import main

ISSUER = "https://keycloak.example.com/realms/nebari"
CLIENT_ID = "default-my-pack"


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


TRUSTED = _keypair()
UNTRUSTED = _keypair()


class FakeJWKClient:
    """Stands in for jwt.PyJWKClient: serves only the trusted public key."""

    def get_signing_key_from_jwt(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "trusted":
            raise jwt.PyJWKClientError("unknown kid")
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(TRUSTED.public_key(), as_dict=True), "kid": "trusted"}
        )


def make_token(key=TRUSTED, kid="trusted", drop=(), **overrides):
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "preferred_username": "alice",
        "email": "alice@example.com",
        "groups": ["admin"],
    }
    claims.update(overrides)
    for name in drop:
        del claims[name]
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "verifier", main.TokenVerifier(ISSUER, CLIENT_ID, FakeJWKClient()))
    return TestClient(main.app)


@pytest.mark.parametrize(
    "name, cookies, authenticated",
    [
        ("valid token", {"IdToken-a1b2c3d4": make_token()}, True),
        ("no cookie", {}, False),
        ("signed by another key", {"IdToken-a1b2c3d4": make_token(key=UNTRUSTED)}, False),
        ("unknown kid", {"IdToken-a1b2c3d4": make_token(kid="other")}, False),
        ("wrong audience", {"IdToken-a1b2c3d4": make_token(aud="someone-else")}, False),
        ("wrong issuer", {"IdToken-a1b2c3d4": make_token(iss="https://evil.example.com")}, False),
        ("expired", {"IdToken-a1b2c3d4": make_token(exp=int(time.time()) - 60)}, False),
        ("no expiry", {"IdToken-a1b2c3d4": make_token(drop=["exp"])}, False),
        ("unsigned payload", {"IdToken-a1b2c3d4": "e30.eyJwcmVmZXJyZWRfdXNlcm5hbWUiOiJldmUifQ."}, False),
        (
            "extra IdToken cookie",
            {"IdToken-a1b2c3d4": make_token(), "IdToken-00000000": make_token(key=UNTRUSTED)},
            False,
        ),
    ],
)
def test_index_only_trusts_verified_tokens(client, name, cookies, authenticated):
    for k, v in cookies.items():
        client.cookies.set(k, v)
    body = client.get("/").text
    assert ("alice" in body) is authenticated, name
    assert ("Not Authenticated" in body) is not authenticated, name


def test_verification_not_configured_fails_closed(monkeypatch):
    monkeypatch.setattr(main, "verifier", None)
    client = TestClient(main.app)
    client.cookies.set("IdToken-a1b2c3d4", make_token())
    body = client.get("/").text
    assert "alice" not in body
    assert "not configured" in body


def test_health():
    assert TestClient(main.app).get("/health").json() == {"status": "ok"}
