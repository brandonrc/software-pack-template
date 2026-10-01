# Example 4: Auth-Aware Helm Pack (FastAPI)

A custom FastAPI application that reads the authenticated user's identity from
the IdToken cookie set by Envoy Gateway after Keycloak OIDC authentication.

## What This Example Shows

- Building a custom container image for a Nebari pack
- Verifying the IdToken cookie against Keycloak before trusting its claims
  (username, email, groups)
- Wiring the operator's OIDC Secret into the app for that verification
- An optional NetworkPolicy so only the Envoy proxies can reach the pods
- How Envoy Gateway handles the OIDC flow before requests reach your app
- Full chart structure with Deployment, Service, and NebariApp

## How Authentication Works

When deployed on Nebari with auth enabled:

1. User visits `my-pack.nebari.example.com`
2. Envoy Gateway intercepts the request (no valid session cookie)
3. User is redirected to Keycloak for login
4. After login, Keycloak redirects back with an authorization code
5. Envoy Gateway exchanges the code for tokens and sets an `IdToken-*` cookie
6. The request (now with the cookie) is forwarded to the FastAPI app
7. The app verifies the JWT from the cookie against Keycloak and displays user info

Your app never handles the login flow - Envoy Gateway does it all. It does have
to verify the resulting cookie before trusting it (see the code walkthrough).

## Deploying to Nebari

### ArgoCD Application (recommended)

The container image is automatically built and published to GHCR by CI when
changes are pushed to `examples/auth-fastapi/app/` or the `Dockerfile`. Create
an ArgoCD Application:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-pack
  namespace: argocd
spec:
  project: nebari-apps   # NIC's AppProject for packs; "default" is deny-all
  source:
    repoURL: https://github.com/YOUR-ORG/YOUR-REPO.git
    targetRevision: main
    path: examples/auth-fastapi/chart
    helm:
      valuesObject:
        nebariapp:
          enabled: true
          hostname: my-pack.nebari.example.com
          auth:
            enabled: true
  destination:
    server: https://kubernetes.default.svc
    namespace: my-pack
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
    # The operator only reconciles NebariApps in namespaces labeled
    # nebari.dev/managed=true. ArgoCD applies this to the namespace it creates.
    managedNamespaceMetadata:
      labels:
        nebari.dev/managed: "true"
```

### Helm install

```bash
helm dependency build ./chart/
helm install my-pack ./chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com \
  --set nebariapp.auth.enabled=true \
  --set networkPolicy.enabled=true
```

The chart passes the app three values for token verification:

| Env var | Source | Override |
|---------|--------|----------|
| `OIDC_CLIENT_ID` | `client-id` in the `<fullname>-oidc-client` Secret | - |
| `OIDC_ISSUER_URL` | `issuer-url` in the same Secret | `oidc.issuerURL` |
| `OIDC_JWKS_URL` | `<issuer>/protocol/openid-connect/certs` | `oidc.jwksURL` |

The operator only fills `issuer-url` when it runs with `KEYCLOAK_EXTERNAL_URL` (NIC sets
it). If it is empty, set `oidc.issuerURL` to the issuer your Keycloak puts in tokens,
or the app shows every user as "Not Authenticated". Set `oidc.jwksURL` to the in-cluster
Keycloak URL if pods can't reach the public one. Env vars are read at startup, so restart
the pods after the Secret or these values change.

## Local development

The container image is pre-built and published to GHCR by CI. You can run it
locally without building:

```bash
docker run -p 8000:8000 ghcr.io/nebari-dev/software-pack-template/auth-fastapi-example:latest
# Open http://localhost:8000 - shows "Not Authenticated" page
```

To build locally from the Dockerfile instead:

```bash
cd examples/auth-fastapi
docker build -t my-pack-fastapi:latest .
docker run -p 8000:8000 my-pack-fastapi:latest
```

## Code Walkthrough

### `app/main.py`

`get_id_token()` returns the JWT from Envoy Gateway's `IdToken-<suffix>` cookie,
and refuses the request if there is more than one such cookie. `TokenVerifier`
then checks the signature against Keycloak's JWKS, plus `iss`, `aud` and `exp`,
before any claim is used.

Envoy Gateway does not verify the JWT signature itself; its OAuth2 filter only reads
the expiry and protects its own cookies with an HMAC. And requests can reach the app
without passing that filter: other pods can call the Service directly, and
`routing.publicRoutes` paths have no SecurityPolicy. So an app that decodes the cookie
without verifying it trusts whatever identity the caller sends. Once verified, the
app reads:

- `preferred_username` - the Keycloak username
- `email` - user's email address
- `name` - display name
- `groups` - Keycloak group memberships
- `realm_access.roles` - Keycloak realm roles

### `app/templates/index.html`

A simple Jinja2 template that renders user information when authenticated, or
an explanatory message saying why no identity is shown (no cookie, verification
failed, or verification not configured).

### `tests/`

Table-driven tests for the verification: valid token, wrong key, unknown key ID, wrong
audience or issuer, expired, unsigned, and an extra `IdToken-*` cookie.

```bash
cd examples/auth-fastapi
pip install -r app/requirements.txt -r tests/requirements.txt
pytest tests
```

## Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI application verifying IdToken cookies |
| `tests/test_main.py` | Tests for the token verification |
| `app/requirements.txt` | Python dependencies |
| `app/templates/index.html` | HTML template for user info display |
| `Dockerfile` | Multi-stage build for the FastAPI image |
| `chart/Chart.yaml` | Helm chart metadata with the nebari-app dependency |
| `chart/values.yaml` | Default config, including the NebariApp spec with auth enabled |
| `chart/templates/_helpers.tpl` | Name, label, and selector helpers |
| `chart/templates/nebariapp.yaml` | Renders the NebariApp via nebari-app |
| `chart/templates/deployment.yaml` | Kubernetes Deployment, with the OIDC env vars when auth is on |
| `chart/templates/networkpolicy.yaml` | Optional NetworkPolicy (`networkPolicy.enabled`) |
| `chart/templates/service.yaml` | ClusterIP Service |
| `chart/templates/NOTES.txt` | Post-install instructions |

See the [main README](../../README.md) for the full customization guide.
