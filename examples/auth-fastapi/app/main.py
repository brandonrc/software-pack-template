"""
Auth-aware FastAPI application for Nebari.

Demonstrates how to read authenticated user identity from the IdToken cookie
set by Envoy Gateway after Keycloak OIDC authentication.

When deployed on Nebari with auth enabled, the Envoy Gateway OIDC filter
handles the login flow and sets an IdToken cookie containing the JWT.
This app verifies that JWT against Keycloak's signing keys before trusting
any claim in it.
"""

import logging
import os
from pathlib import Path

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

app = FastAPI(title="Auth-Aware Nebari Pack")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class TokenVerifier:
    """Verifies IdTokens issued by the Keycloak realm for this app's OIDC client.

    Envoy Gateway does NOT verify the JWT signature: its OAuth2 filter only
    reads the token's expiry and protects its own cookies with an HMAC. Requests
    can also reach the app without passing that filter at all (in-cluster
    traffic to the Service, or paths in routing.publicRoutes), carrying any
    cookie the caller likes. So the app has to check the signature, issuer,
    audience and expiry itself.
    """

    def __init__(self, issuer: str, client_id: str, jwks_client):
        self.issuer = issuer
        self.client_id = client_id
        self.jwks_client = jwks_client

    def verify(self, token: str) -> dict | None:
        try:
            key = self.jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                # PyJWT rejects a missing iss/aud when they are passed above,
                # but accepts a token with no exp unless told to require it.
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWKClientConnectionError as exc:
            # Keycloak's JWKS endpoint is unreachable. This is an outage, not a
            # bad token, so make it visible; the request still fails closed.
            log.warning("could not fetch signing keys to verify IdToken: %s", exc)
            return None
        except jwt.PyJWTError as exc:
            log.info("rejected IdToken: %s", exc)
            return None


def verifier_from_env() -> TokenVerifier | None:
    """Build a verifier from the values the Helm chart injects.

    OIDC_CLIENT_ID and OIDC_ISSUER_URL come from the operator-created
    <nebariapp-name>-oidc-client Secret (keys client-id and issuer-url).
    OIDC_JWKS_URL defaults to Keycloak's certs endpoint under the issuer; set it
    to the in-cluster Keycloak URL when pods cannot reach the public one.
    """
    issuer = os.environ.get("OIDC_ISSUER_URL", "")
    client_id = os.environ.get("OIDC_CLIENT_ID", "")
    if not issuer or not client_id:
        return None
    jwks_url = os.environ.get("OIDC_JWKS_URL") or f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
    return TokenVerifier(issuer, client_id, jwt.PyJWKClient(jwks_url, timeout=5))


verifier = verifier_from_env()


def get_id_token(request: Request) -> str | None:
    """Return the IdToken cookie set by Envoy Gateway's OIDC filter.

    Envoy Gateway names the cookie IdToken-<suffix>, where <suffix> is generated
    per SecurityPolicy, so the app matches on the prefix. Exactly one such
    cookie is expected; if there are more, someone added one, and the request
    is treated as unauthenticated rather than guessing which to trust.
    """
    tokens = [value for name, value in request.cookies.items() if name.startswith("IdToken-")]
    if len(tokens) != 1:
        return None
    return tokens[0]


@app.get("/health")
def health():
    """Health check endpoint for Kubernetes probes."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Main page showing authenticated user information."""
    token = get_id_token(request)
    user_info = None
    reason = "No IdToken cookie found."

    if verifier is None:
        reason = "Token verification is not configured (OIDC_ISSUER_URL and OIDC_CLIENT_ID are unset)."
    elif token:
        claims = verifier.verify(token)
        if claims is None:
            reason = "The IdToken cookie could not be verified."
        else:
            user_info = {
                "username": claims.get("preferred_username", "unknown"),
                "email": claims.get("email", ""),
                "name": claims.get("name", ""),
                "groups": claims.get("groups", []),
                "roles": claims.get("realm_access", {}).get("roles", []),
            }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user_info": user_info,
            "authenticated": user_info is not None,
            "reason": reason,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
