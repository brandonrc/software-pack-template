# Nebari Software Pack Template

[![Lint](https://github.com/nebari-dev/software-pack-template/actions/workflows/lint.yaml/badge.svg)](https://github.com/nebari-dev/software-pack-template/actions/workflows/lint.yaml)
[![Test](https://github.com/nebari-dev/software-pack-template/actions/workflows/test.yaml/badge.svg)](https://github.com/nebari-dev/software-pack-template/actions/workflows/test.yaml)
[![Integration Test](https://github.com/nebari-dev/software-pack-template/actions/workflows/test-integration.yaml/badge.svg)](https://github.com/nebari-dev/software-pack-template/actions/workflows/test-integration.yaml)

A template repository for building **Nebari Software Packs** - Kubernetes
applications that deploy on the [Nebari](https://nebari.dev) platform with
optional routing, TLS, and OIDC authentication.

## Table of Contents

- [What is a Nebari Software Pack?](#what-is-a-nebari-software-pack)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Repository Structure](#repository-structure)
- [The NebariApp CRD](#the-nebariapp-crd)
- [Example 1: Vanilla YAML (Plain Manifests)](#example-1-vanilla-yaml-plain-manifests)
- [Example 2: Kustomize (Nginx)](#example-2-kustomize-nginx)
- [Example 3: Helm - Basic Pack (Nginx)](#example-3-helm---basic-pack-nginx)
- [Example 4: Helm - Auth-Aware Pack (FastAPI)](#example-4-helm---auth-aware-pack-fastapi)
- [Example 5: Helm - Wrapping an Existing Chart (Podinfo)](#example-5-helm---wrapping-an-existing-chart-podinfo)
- [How Authentication Works](#how-authentication-works)
- [Local Development](#local-development)
- [CI/CD Pipeline](#cicd-pipeline)
- [Deploying to a Nebari Cluster](#deploying-to-a-nebari-cluster)
- [Customizing for Your Own Application](#customizing-for-your-own-application)
- [Troubleshooting](#troubleshooting)

## What is a Nebari Software Pack?

A **software pack** is any Kubernetes deployment that includes a **NebariApp**
custom resource. The NebariApp tells the
[nebari-operator](https://github.com/nebari-dev/nebari-operator) to auto-configure:

- **Routing** - Creates an HTTPRoute on the shared Envoy Gateway
- **TLS** - Provisions a certificate via cert-manager
- **Authentication** - Sets up Keycloak OIDC via an Envoy Gateway SecurityPolicy

The NebariApp CRD is the integration point between your application and the
Nebari platform. How you deploy the rest of your application is up to you -
**Helm charts, Kustomize overlays, and plain YAML manifests** are all supported.
All three are first-class deployment methods in ArgoCD.

```mermaid
graph LR
    User -->|HTTPS| EG[Envoy Gateway]
    EG -->|No session?| KC[Keycloak]
    KC -->|Auth code| EG
    EG -->|IdToken cookie| HR[HTTPRoute]
    HR --> SVC[Service]
    SVC --> Pod

    style EG fill:#e1f5fe
    style KC fill:#fff3e0
    style HR fill:#e8f5e9
```

## Prerequisites

- [kubectl](https://kubernetes.io/docs/tasks/tools/)

Depending on your deployment method:
- [Helm 3](https://helm.sh/docs/intro/install/) (for Helm examples)
- [Docker](https://docs.docker.com/get-docker/) (for building custom images)

For local development (optional):
- [kind](https://kind.sigs.k8s.io/)

## Getting Started

1. **Use this template** - Click "Use this template" on GitHub to create your own repo

2. **Clone your new repo**
   ```bash
   git clone https://github.com/YOUR-ORG/YOUR-REPO.git
   cd YOUR-REPO
   ```

3. **Pick an example** that matches your use case:

   **No tooling dependencies:**
   - `examples/vanilla-yaml/` - Plain YAML manifests, just `kubectl apply`
   - `examples/kustomize-nginx/` - Kustomize overlays for per-environment config

   **Helm-based:**
   - `examples/basic-nginx/` - Simplest possible Helm chart
   - `examples/auth-fastapi/` - Custom app that reads auth tokens
   - `examples/wrap-existing-chart/` - Wrapping an existing Helm chart (most common for Helm)

4. **Search and replace** `my-pack` with your pack name:
   ```bash
   # Preview changes
   grep -r "my-pack" examples/vanilla-yaml/

   # Replace (using your pack name)
   find . -type f -name "*.yaml" -o -name "*.tpl" -o -name "*.txt" -o -name "Makefile" | \
     xargs sed -i 's/my-pack/your-pack-name/g'
   ```

5. **Deploy locally** to test:
   ```bash
   # Vanilla YAML (simplest)
   kubectl apply -f examples/vanilla-yaml/deployment.yaml \
                 -f examples/vanilla-yaml/service.yaml
   kubectl port-forward svc/my-pack 8080:80

   # Or with Helm (fetch the nebari-app dependency first)
   helm dependency build examples/basic-nginx/chart/
   helm install test examples/basic-nginx/chart/
   kubectl port-forward svc/test-my-pack 8080:80

   # Open http://localhost:8080
   ```

## Repository Structure

```
software-pack-template/
  .github/workflows/
    build-images.yaml            # Build + publish images via reusable pack-build-image
    lint.yaml                    # Manifest validation (all examples)
    test.yaml                    # Integration tests on kind cluster
    test-integration.yaml        # NebariApp integration tests (full stack)
    release.yaml                 # Release chart via reusable pack-release workflow
  examples/
    vanilla-yaml/                # Example 1: Plain Kubernetes manifests
      deployment.yaml            # nginx Deployment
      service.yaml               # ClusterIP Service
      nebariapp.yaml             # NebariApp CRD resource
      README.md
    kustomize-nginx/             # Example 2: Kustomize-based pack
      base/
        kustomization.yaml       # References base resources
        deployment.yaml
        service.yaml
        nebariapp.yaml
      overlays/
        dev/                     # Dev overlay: dev hostname, no auth
          kustomization.yaml
          nebariapp-patch.yaml
        production/              # Prod overlay: prod hostname, auth enabled
          kustomization.yaml
          nebariapp-patch.yaml
      README.md
    basic-nginx/                 # Example 3: Simplest Helm chart
      chart/
        Chart.yaml               # Has nebari-app as a dependency
        Chart.lock
        values.yaml              # NebariApp config under nebariapp:
        templates/
          _helpers.tpl           # Name, label, selector helpers
          nebariapp.yaml         # Renders the NebariApp via nebari-app (conditional)
          deployment.yaml        # Kubernetes Deployment
          service.yaml           # ClusterIP Service
          NOTES.txt              # Post-install instructions
      README.md
    auth-fastapi/                # Example 4: Custom app reading IdToken
      app/
        main.py                  # FastAPI verifying the IdToken cookie
        requirements.txt
        templates/index.html     # User info display
      tests/                     # pytest suite for the token verification
      Dockerfile
      chart/                     # basic-nginx structure, plus OIDC env wiring
                                 # and an optional networkpolicy.yaml
      README.md
    wrap-existing-chart/         # Example 5: Wrapping podinfo via Helm
      chart/
        Chart.yaml               # Has podinfo and nebari-app as dependencies
        Chart.lock
        values.yaml              # Podinfo overrides + NebariApp config
        templates/
          _helpers.tpl
          nebariapp.yaml         # Renders the NebariApp via nebari-app
          NOTES.txt
      README.md
  dev/
    Makefile                     # Local dev with full Nebari stack on kind
    configure-operator.sh        # Configures the operator the way NIC does
    keycloak-route.yaml          # Exposes Keycloak at keycloak.nebari.local
    verify-nebariapp.sh          # Checks an app is served, not just Ready
    login-test.sh                # Logs in with curl and checks the app
    .cache/                      # (gitignored) Cloned nebari-operator scripts
  docs/
    nebariapp-crd-reference.md   # Where the field reference lives, plus deployment patterns
    auth-flow.md                 # Authentication flow details
  .gitignore
  .editorconfig
  LICENSE                        # Apache 2.0
  README.md                      # This file
```

## The NebariApp CRD

The **NebariApp** custom resource is the integration point between your pack and the
Nebari platform. When you create a NebariApp, the
[nebari-operator](https://github.com/nebari-dev/nebari-operator) watches for it and
automatically configures routing, TLS, and authentication.

Here's a fully annotated example:

```yaml
apiVersion: reconcilers.nebari.dev/v1
kind: NebariApp
metadata:
  name: my-pack
spec:
  # The domain where your app will be accessible
  hostname: my-pack.nebari.example.com

  # The Kubernetes Service that should receive traffic
  service:
    name: my-pack           # Service name in the same namespace
    port: 80                # Service port (1-65535)

  # Routing: required in practice. Without it the operator creates no
  # HTTPRoute and no TLS, yet the NebariApp still reports Ready.
  routing:
    routes:
      - pathPrefix: /       # Match all paths (default behavior)
        pathType: PathPrefix # PathPrefix or Exact
    tls:
      enabled: true         # Auto-provision TLS certificate (default: true)

  # Optional: OIDC authentication
  auth:
    enabled: true                   # Require login (default: false)
    provider: keycloak              # keycloak or generic-oidc
    provisionClient: true           # Auto-create Keycloak client (default: true)
    # redirectURI: /oauth2/callback # OAuth callback path (default shown; rarely needs overriding)
    scopes:                         # OIDC scopes to request
      - openid
      - profile
      - email
    groups:                         # NOT enforced by the gateway in v0.1.1; see below
      - admin
    enforceAtGateway: true          # Create SecurityPolicy at gateway (default: true)

  # Which gateway to use: "public" (default) or "internal"
  gateway: public
```

The NebariApp is just a Kubernetes resource. It can live in a plain YAML file, a
Kustomize base, or a Helm template.

With plain YAML or Kustomize, the NebariApp manifest is always present. When
deploying standalone, simply skip that file or exclude it from your apply command.

### NebariApp in Helm charts

To render the NebariApp in a Helm chart with the official
[`nebari-app` library chart](https://github.com/nebari-dev/nebari-operator/tree/main/charts/nebari-app):

1. Add it as a dependency in `Chart.yaml`, then run `helm dependency build`:

   ```yaml
   dependencies:
     - name: nebari-app
       repository: oci://quay.io/nebari/charts
       version: ">=0.1.1"
   ```

2. Set any NebariApp `spec` field under `nebariapp:` in `values.yaml`:

   ```yaml
   nebariapp:
     enabled: false
     hostname: '{{ fail "nebariapp.hostname is required when nebariapp.enabled is true" }}'
     service:
       name: '{{ include "my-pack.fullname" . | toJson }}'
       port: '{{ .Values.service.port }}'
     routing:
       routes:
         - pathPrefix: /
       tls:
         enabled: true
   ```

3. Render it in `templates/nebariapp.yaml`. The `if` makes the NebariApp optional,
   so the chart works both standalone and on Nebari:

   ```yaml
   {{- if .Values.nebariapp.enabled }}
   {{- include "nebari-app.nebariApp" (dict
       "metadata" (dict
         "name"      (include "my-pack.fullname" .)
         "namespace" .Release.Namespace
         "labels"    (include "my-pack.labels" . | fromYaml)
       )
       "spec"   (omit .Values.nebariapp "enabled")
       "tplCtx" .
   ) -}}
   {{- end }}
   ```

When a `{{ ... }}` value renders a string, add `| toJson` at the end. It wraps the string in quotes so it's valid JSON:

```yaml
name: '{{ include "my-pack.fullname" . | toJson }}'   # works
name: '{{ include "my-pack.fullname" . }}'            # fails to render
```

### Beyond the basics

The fields shown above cover the common cases. The operator also supports several
more specialized features. Each is documented in the
[NebariApp API reference](https://github.com/nebari-dev/nebari-operator/blob/v0.1.1/docs/api-reference.md), generated from the operator's Go types:

- **`routing.publicRoutes`** - paths that bypass OIDC auth (e.g., `/healthz`,
  webhooks, public APIs).
- **`routing.annotations`** - extra annotations on the generated HTTPRoute, useful
  for ArgoCD tracking or other tooling.
- **`auth.forwardAccessToken`** - send the user's access token to your app as
  `Authorization: Bearer <token>` so it can decode the JWT itself.
- **`auth.denyRedirect`** - return 401 instead of redirecting to Keycloak when
  matching headers are present. Most useful alongside `auth.spaClient` to avoid
  PKCE races when an SPA fires several requests on page load.
- **`auth.spaClient`** - provision a separate public Keycloak client for browser-
  based PKCE flows (React + `keycloak-js`, etc.).
- **`auth.deviceFlowClient`** - provision a public client for the OAuth2 Device
  Authorization Grant (CLIs and native apps).
- **`auth.keycloakConfig`** - declaratively manage Keycloak realm groups and
  client-level protocol mappers from the NebariApp.
- **`auth.tokenExchange`** - opt this app into RFC 8693 token exchange so other
  NebariApp clients can mint tokens for its audience.
- **`landingPage`** - register the app on the Nebari landing page with an icon,
  category, priority, and optional health check.
- **`serviceAccountName`** - which ServiceAccount the operator should grant access
  to the OIDC client Secret.
- **`service.namespace`** - point the NebariApp at a Service in a different
  namespace.

For the complete field reference, see the [NebariApp API reference](https://github.com/nebari-dev/nebari-operator/blob/v0.1.1/docs/api-reference.md).

## Example 1: Vanilla YAML (Plain Manifests)

The simplest possible pack. Plain Kubernetes manifests with no tooling
dependencies beyond `kubectl`.

**What it demonstrates:**
- Lowest barrier to entry
- NebariApp as a plain YAML file alongside Deployment and Service
- No templating or tooling required

```bash
# Deploy standalone (skip the NebariApp)
kubectl apply -f examples/vanilla-yaml/deployment.yaml \
              -f examples/vanilla-yaml/service.yaml
kubectl port-forward svc/my-pack 8080:80
# Open http://localhost:8080

# Deploy on Nebari (edit nebariapp.yaml hostname first)
kubectl apply -f examples/vanilla-yaml/
```

See [examples/vanilla-yaml/README.md](examples/vanilla-yaml/README.md) for the full walkthrough.

## Example 2: Kustomize (Nginx)

Uses [Kustomize](https://kustomize.io/) overlays to manage environment-specific
NebariApp configuration. Same nginx app as the vanilla example, but with
structured per-environment patches.

**What it demonstrates:**
- Kustomize base with overlays for dev and production
- Patching hostname and auth settings per environment
- No Helm dependency

```bash
# Preview the dev overlay
kubectl kustomize examples/kustomize-nginx/overlays/dev/

# Deploy the dev overlay on Nebari
kubectl apply -k examples/kustomize-nginx/overlays/dev/

# Deploy the production overlay (auth enabled)
kubectl apply -k examples/kustomize-nginx/overlays/production/
```

See [examples/kustomize-nginx/README.md](examples/kustomize-nginx/README.md) for the full walkthrough.

## Example 3: Helm - Basic Pack (Nginx)

The simplest possible Helm-based pack. Deploys a stock nginx container with
optional Nebari integration via the `nebari-app` library chart.

**What it demonstrates:**
- Minimum viable Helm chart structure
- Conditional NebariApp template (`nebariapp.enabled` toggle)
- Toggling between standalone and Nebari modes

```bash
# Fetch the nebari-app dependency
helm dependency build examples/basic-nginx/chart/

# Deploy standalone
helm install test-basic examples/basic-nginx/chart/
kubectl port-forward svc/test-basic-my-pack 8080:80
# Open http://localhost:8080

# Deploy on Nebari
helm install my-pack examples/basic-nginx/chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com

# Deploy on Nebari with auth
helm install my-pack examples/basic-nginx/chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com \
  --set nebariapp.auth.enabled=true
```

See [examples/basic-nginx/README.md](examples/basic-nginx/README.md) for the full walkthrough.

## Example 4: Helm - Auth-Aware Pack (FastAPI)

A custom Python app that reads the IdToken cookie set by Envoy Gateway after
Keycloak authentication. Shows how to consume authenticated user identity safely.

**What it demonstrates:**
- Building a custom container image
- Verifying the IdToken's signature, issuer and audience against Keycloak before
  trusting any claim (Envoy Gateway does not verify it, and requests can reach the
  app without passing the gateway)
- Wiring the client ID and issuer from the operator's OIDC Secret in the chart
- An optional NetworkPolicy that only lets the Envoy proxies reach the pods
- Rendering user info (username, email, groups)

The key code in `app/main.py`:

```python
claims = jwt.decode(
    token,
    jwks_client.get_signing_key_from_jwt(token).key,
    algorithms=["RS256"],
    audience=CLIENT_ID,   # client-id from <name>-oidc-client
    issuer=ISSUER,        # issuer-url from <name>-oidc-client
)
```

```bash
# Run locally (shows "Not Authenticated" - no IdToken cookie without Envoy Gateway)
docker run -p 8000:8000 ghcr.io/nebari-dev/software-pack-template/auth-fastapi-example:latest

# Deploy on Nebari with auth
helm dependency build examples/auth-fastapi/chart/
helm install my-pack examples/auth-fastapi/chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com \
  --set networkPolicy.enabled=true
```

See [examples/auth-fastapi/README.md](examples/auth-fastapi/README.md) for the full walkthrough.

## Example 5: Helm - Wrapping an Existing Chart (Podinfo)

**This is the most realistic Helm use case.** Most Helm-based packs wrap
existing software - you don't write your own Deployment or Service. You add
the upstream chart as a dependency and point the NebariApp at its service.

**What it demonstrates:**
- Chart.yaml dependency on an existing chart
- Overriding upstream values
- NebariApp pointing to the upstream service
- No custom Deployment or Service templates needed

```yaml
# Chart.yaml - add the upstream chart next to nebari-app
dependencies:
  - name: nebari-app
    repository: oci://quay.io/nebari/charts
    version: ">=0.1.1"
  - name: podinfo
    version: 6.10.1
    repository: oci://ghcr.io/stefanprodan/charts
```

The NebariApp points to podinfo's service from `values.yaml`:

```yaml
nebariapp:
  service:
    name: '{{ printf "%s-podinfo" .Release.Name | toJson }}'   # Upstream service
    port: 9898
  routing:
    routes:
      - pathPrefix: /
    tls:
      enabled: true
```

**You don't rewrite the app. You just connect it to Nebari.**

```bash
# Build dependencies
helm dependency build examples/wrap-existing-chart/chart/

# Deploy standalone
helm install test-wrap examples/wrap-existing-chart/chart/
kubectl port-forward svc/test-wrap-podinfo 9898:9898

# Deploy on Nebari
helm install my-pack examples/wrap-existing-chart/chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com
```

See [examples/wrap-existing-chart/README.md](examples/wrap-existing-chart/README.md) for the full walkthrough.

## How Authentication Works

When a NebariApp has `auth.enabled: true`, the nebari-operator creates an Envoy
Gateway SecurityPolicy that handles the full OIDC flow:

```
1. User visits my-pack.nebari.example.com
2. Envoy Gateway checks for a valid session cookie
   - No cookie? Redirect to Keycloak login page
3. User authenticates with Keycloak
4. Keycloak redirects back with an authorization code
5. Envoy Gateway exchanges the code for tokens
6. Envoy Gateway sets cookies:
   - IdToken-<suffix>     (JWT with user claims)
   - AccessToken-<suffix>
   - RefreshToken-<suffix>
   (<suffix> is an 8-char, lower-case hex derived from the SecurityPolicy UID via FNV-32a)
7. Request (now with cookies) is forwarded to your app
```

**What the operator automates:**
- Creates a Keycloak OIDC client (when `provisionClient: true`)
- Stores client credentials in a Kubernetes Secret
- Creates an Envoy Gateway SecurityPolicy with the OIDC configuration
- Creates an HTTPRoute directing traffic to your service
- Provisions a TLS certificate via cert-manager

**What the operator does not do:**
- Enforce `auth.groups`. Any user who can log in to the realm reaches the app
  ([nebari-operator#153](https://github.com/nebari-dev/nebari-operator/issues/153)).
- Verify the IdToken for your app. Envoy Gateway never checks the JWT signature,
  and requests can reach the app without passing the gateway: from other pods,
  and on `routing.publicRoutes` paths, which have no SecurityPolicy.

**What your app must do before trusting an identity:**
- Read the single `IdToken-*` cookie (see Example 4)
- Verify its signature against Keycloak's JWKS, and check `iss`, `aud` and `exp`
- Only then use claims such as `preferred_username`, `email` and `groups`, for
  example to restrict access to a group

**If your app handles OAuth natively** (like Grafana), set `enforceAtGateway: false`.
The operator will still provision the OIDC client and store credentials in a Secret,
but won't create a SecurityPolicy. Your app reads the credentials from the Secret
and handles the OAuth flow itself.

For more details, see [docs/auth-flow.md](docs/auth-flow.md).

## Local Development

The `dev/` directory provides a Makefile for local development with
[kind](https://kind.sigs.k8s.io/). Running any `up-*` target automatically
creates a kind cluster with the full Nebari infrastructure stack - MetalLB,
Envoy Gateway, cert-manager, Keycloak, and the nebari-operator - so every
example deploys with NebariApp enabled, routing, TLS, and authentication
working just like a real Nebari cluster. The operator is configured the way NIC
configures it, and Keycloak is exposed at `keycloak.nebari.local`, so the full
login works locally (user `admin`, password `nebari-admin`).

The first `make up-*` run takes ~5-10 minutes (cluster and infrastructure
setup). Subsequent runs reuse the existing cluster and are fast.

```bash
cd dev

# Deploy vanilla YAML example
make up-vanilla

# Deploy kustomize example (dev overlay)
make up-kustomize

# Deploy Helm nginx example
make up-basic

# Deploy podinfo Helm example
make up-podinfo

# Deploy FastAPI Helm example (auth enabled, uses pre-built GHCR image)
make up-fastapi

# Log in to the FastAPI example with curl and check the app verified the token
make login-test

# Update /etc/hosts with NebariApp and Keycloak hostnames
make update-hosts

# Delete the kind cluster
make down
```

Each `up-*` target deploys with NebariApp enabled at `https://my-pack.nebari.local`,
checks that the app is actually served through the Gateway (`RoutingReady`,
`TLSReady` and a real HTTPS request, not just `Ready`), and updates `/etc/hosts` so
you can access the app in your browser. Run `make update-hosts` once to add
`keycloak.nebari.local` too, so the browser can reach the login page. The dev CA is
self-signed, so expect a certificate warning.

The `/etc/hosts` steps use `sudo`. Everything else works without it:
`make login-test` and `dev/verify-nebariapp.sh` resolve hostnames themselves.

### What's not included

The local dev environment does not include ArgoCD. If you need to develop or
test the ArgoCD Application that wraps your software pack, you'll need to set
that up separately. In the future, Nebari will support pointing at a local Git
repo (and creating a temporary one if none is provided) so ArgoCD-based
workflows can be tested locally without an external repository.

## CI/CD Pipeline

### Lint (`lint.yaml`)

Runs on every push and PR. Validates all examples:

- `kubectl apply --dry-run=client` for the vanilla YAML example
- `kubectl kustomize` for each Kustomize overlay
- `helm lint` and `helm template` for each Helm chart (both NebariApp enabled and disabled)
- Fails if any example chart renders a NebariApp without `spec.routing`
- Fails if any ArgoCD Application example uses `project: default` (deny-all on NIC)
- Runs the auth-fastapi token verification tests (`pytest`)

### Build Images (`build-images.yaml`)

Calls the shared reusable workflow
`nebari-dev/.github/.github/workflows/pack-build-image.yaml@v1` to build and
publish the auth-fastapi example image to GHCR
(`ghcr.io/nebari-dev/software-pack-template/auth-fastapi-example`) and quay.io,
tagged `sha-<short>` and `latest`. Runs on pushes to main that modify the
example's `app/`, `Dockerfile`, or its chart `Chart.yaml` (so the release commit
produces the sha-pinned image the release workflow references), on pull requests
(build only, no push), and on manual dispatch.

> **Manual prerequisite (official packs).** The shared workflow does **not**
> create Quay repositories. Before the first push, a maintainer must create the
> image's Quay repository under the `nebari` org and grant the CI robot account
> write access (make it public if the pack is public). For this example that is
> `quay.io/nebari/software-pack-template-auth-fastapi-example`; in general it is
> `quay.io/nebari/<repo-name>-<image>`. GHCR repositories are created
> automatically on first push, so only Quay needs this step. The `QUAY_TOKEN`,
> `QUAY_USERNAME`, and `NEBARI_HELM_REPO_TOKEN` secrets are provided
> organizationally to public nebari-dev repos.

### Test (`test.yaml`)

Runs on every push and PR. Standalone integration tests on a kind cluster:

- Creates a kind cluster
- Deploys each example with `nebariapp.enabled=false` (no operator required)
- Waits for pods and runs HTTP health checks via port-forward
- Validates that each example works as a standalone Kubernetes deployment

### Integration Test (`test-integration.yaml`)

Runs on pushes to main and PRs that modify `examples/`, `dev/`, or the workflow
file. Tests each example with `nebariapp.enabled=true` on a full Nebari
infrastructure stack:

- Creates a kind cluster with MetalLB, Envoy Gateway, cert-manager, and Keycloak
- Runs once per operator version: `v0.1.1` (the release these docs track) and
  `v0.1.0-alpha.20` (what NIC v0.14.0 deploys)
- Configures the operator the way NIC does (`dev/configure-operator.sh`)
- Deploys each example with NebariApp enabled and a `*.nebari.local` hostname
- Verifies `RoutingReady` and `TLSReady`, that the HTTPRoute exists, and that a real
  HTTPS request through the Gateway succeeds (`dev/verify-nebariapp.sh`)
- For auth-enabled examples, verifies `AuthReady`, the SecurityPolicy, and the
  redirect to Keycloak; for auth-fastapi, logs in end to end and checks that forged
  tokens sent around the gateway are rejected

This catches bugs in NebariApp configuration, operator compatibility, and routing
setup that the standalone test cannot detect.

### Release (`release.yaml`)

Calls the shared reusable workflow
`nebari-dev/.github/.github/workflows/pack-release.yaml@v1` when the auth-fastapi
example's `Chart.yaml` version changes on main. The reusable workflow pins the
chart's image tag to the release commit's sha, packages the chart, creates a
GitHub Release, and opens a PR in `nebari-dev/helm-repository` (which publishes
to `quay.io/nebari/charts`).

This flow works only for official packs in the nebari-dev org: it relies on the
org's `NEBARI_HELM_REPO_TOKEN` secret and the central `nebari-dev/helm-repository`.
Forks outside the org must supply their own publishing infrastructure.

## Deploying to a Nebari Cluster

### Option A: ArgoCD Application (recommended)

ArgoCD supports all three deployment methods. Set the `source` section based on
your pack type:

**ArgoCD with Helm:**

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
    path: examples/basic-nginx/chart    # or your chart path
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

**ArgoCD with Kustomize:**

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
    path: examples/kustomize-nginx/overlays/production
    # ArgoCD auto-detects kustomization.yaml
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

**ArgoCD with plain YAML (directory):**

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
    path: examples/vanilla-yaml
    directory:
      recurse: false
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

### Option B: kubectl apply (plain YAML)

```bash
# Edit nebariapp.yaml with your hostname first
kubectl apply -f examples/vanilla-yaml/ \
  --namespace my-pack
```

### Option C: kubectl apply -k (Kustomize)

```bash
kubectl apply -k examples/kustomize-nginx/overlays/production/ \
  --namespace my-pack
```

### Option D: Helm install

```bash
helm dependency build ./chart/
helm install my-pack ./chart/ \
  --namespace my-pack \
  --create-namespace \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com \
  --set nebariapp.auth.enabled=true
```

### Verifying the deployment

```bash
# Check the NebariApp status
kubectl get nebariapp -n my-pack

# Check conditions (should all be True when ready)
kubectl describe nebariapp my-pack -n my-pack

# Expected conditions:
#   RoutingReady: True    - HTTPRoute created
#   TLSReady: True        - Certificate provisioned (False/ClusterIssuerNotConfigured
#                           if the operator has no ClusterIssuer; the shared
#                           wildcard listener still serves HTTPS)
#   AuthReady: True       - SecurityPolicy created (if auth enabled)
#   Ready: True           - Core checks passed. Does NOT wait for the others:
#                           a NebariApp with no routing is Ready and unreachable.
```

## Customizing for Your Own Application

### Search and replace

| Token | Replace with | Where |
|-------|-------------|-------|
| `my-pack` | Your pack name (lowercase, hyphenated) | All YAML files, chart files, Makefile |
| `OWNER/REPO` or `YOUR-ORG/YOUR-REPO` | Your GitHub org/repo | Workflows, README |

The placeholder `my-pack` is valid YAML/Helm syntax, so linting passes on the
template repo as-is.

### Replacing the container image

In `values.yaml` (Helm) or directly in `deployment.yaml` (vanilla/Kustomize):

```yaml
# Helm values.yaml
image:
  repository: your-registry/your-image
  tag: "1.0.0"
```

```yaml
# Plain YAML or Kustomize deployment.yaml
containers:
  - name: your-app
    image: your-registry/your-image:1.0.0
```

### Adding resources

Common additions:

- **ConfigMap** - Configuration files mounted into pods
- **Secret** - Credentials (in Helm, use `lookup()` for ArgoCD safety)
- **PersistentVolumeClaim** - Persistent storage
- **ServiceAccount** - Pod identity for RBAC

For Helm charts, add these to `templates/`. For Kustomize, add them to `base/`
and reference them in `kustomization.yaml`. For vanilla YAML, add them as
additional files.

### Multiple routes

If your app serves multiple paths:

```yaml
# In the NebariApp spec (any deployment method)
routing:
  routes:
    - pathPrefix: /api
      pathType: PathPrefix
    - pathPrefix: /dashboard
      pathType: PathPrefix
```

### Restricting access to specific groups

`auth.groups` does **not** restrict access in operator v0.1.1. The operator
creates the groups in Keycloak and lists them on the landing page, but the
SecurityPolicy it generates has no authorization rule, so every user who can log
in to the realm reaches the app
([nebari-operator#153](https://github.com/nebari-dev/nebari-operator/issues/153)).

Until that is fixed, check group membership in your app: verify the IdToken (see
Example 4 and [docs/auth-flow.md](docs/auth-flow.md)) and reject users whose
`groups` claim doesn't include the group you need.

## Troubleshooting

### NebariApp shows `NamespaceNotOptedIn`

The namespace needs the label that opts it in for nebari-operator processing:

```bash
kubectl label namespace my-pack nebari.dev/managed=true
```

### NebariApp shows `ServiceNotFound`

The NebariApp's `spec.service.name` doesn't match any Service in the namespace.
Check the service name:

```bash
kubectl get svc -n my-pack
```

For Helm-based wrapped charts, the service name follows the upstream chart's
naming convention (usually `<release>-<chart-name>`).

### Auth not working / no redirect to Keycloak

1. Check that `auth.enabled` is `true` in the NebariApp spec
2. Check that the nebari-operator is running:
   ```bash
   kubectl get pods -n nebari-operator-system
   ```
3. Check the NebariApp conditions:
   ```bash
   kubectl describe nebariapp my-pack -n my-pack
   ```
   Look for `AuthReady` condition.

### Every path returns 500 with auth enabled

Envoy Gateway rejected the generated SecurityPolicy. Check its status:

```bash
kubectl get securitypolicy my-pack-security -n my-pack \
  -o jsonpath='{range .status.ancestors[*].conditions[*]}{.type}={.status}: {.message}{"\n"}{end}'
```

`OIDC: error fetching endpoints from issuer` means the operator is pointing Envoy
Gateway at the wrong in-cluster Keycloak URL. Set `KEYCLOAK_ISSUER_SERVICE_PORT` and
`KEYCLOAK_ISSUER_CONTEXT_PATH` on the operator Deployment to match your Keycloak
Service (NIC sets these; `dev/configure-operator.sh` shows the kind equivalent).

### TLS certificate not provisioning

1. Check the `TLSReady` reason. `ClusterIssuerNotConfigured` means the operator has
   no `TLS_CLUSTER_ISSUER_NAME`; the app is then served by the Gateway's shared
   listener and certificate.
2. Check cert-manager is running:
   ```bash
   kubectl get pods -n cert-manager
   ```
3. Check the Certificate resource. Certificates live in the Gateway's namespace:
   ```bash
   kubectl get certificate -n envoy-gateway-system
   kubectl describe certificate my-pack-my-pack-cert -n envoy-gateway-system
   ```

### No IdToken cookie in the app

1. Ensure you're accessing through the configured hostname (not via port-forward)
2. Check that the SecurityPolicy was created:
   ```bash
   kubectl get securitypolicy -n my-pack
   ```
3. Check Envoy Gateway logs:
   ```bash
   kubectl logs -n envoy-gateway-system deploy/envoy-gateway
   ```

### `missing in charts/ directory: nebari-app`

The Helm examples depend on the `nebari-app` chart. Fetch it before installing:

```bash
helm dependency build examples/basic-nginx/chart/
```

### `helm dependency build` fails

The dependencies are pulled from OCI registries, so ensure Helm 3.8+ is installed:

```bash
helm version
helm dependency build examples/wrap-existing-chart/chart/
```

## Documentation Portal

Pack docs are served at `packs.nebari.dev/<repo-short-name>/`. The portal routes each
pack's docs site transparently via a Cloudflare edge Worker so users see a single
unified domain, while each pack deploys and previews independently.

### Opting in

1. **Add `docs_site: true` to `pack-metadata.yaml`:**

   ```yaml
   docs_site: true
   links:
     docs: https://packs.nebari.dev/<your-repo-name>/
   ```

2. **Copy `.github/workflows/docs.yml` and `.github/workflows/docs-preview-cleanup.yml`
   from this repo** into your pack repo and update these values:

   | Variable | In | Set to |
   |----------|----|--------|
   | `PACK_SLUG` (env) | `docs.yml` | your repo's short name (the segment after `nebari-dev/`) |
   | `--project-name=...` in the `wrangler` command | `docs.yml` | the matching Cloudflare Pages project name |
   | `CF_PROJECT` (env) | `docs-preview-cleanup.yml` | the same Cloudflare Pages project name |

   For most packs, `PACK_SLUG` and the project name are the same (e.g., `llm-serving-pack`).
   The template repo is a special case: `PACK_SLUG: building-a-software-pack` routes to
   `packs.nebari.dev/building-a-software-pack/` but deploys to the `nebari-software-pack-template`
   CF Pages project.

3. **Add your pack to `tracked-packs.yaml`** in
   [software-pack-dashboard](https://github.com/nebari-dev/software-pack-dashboard) if it is
   not already there. The dashboard schema is at
   `nebari-dev/software-pack-dashboard/schema/pack-metadata.schema.json`.

4. **Set `params.logoLink = "https://packs.nebari.dev/"` in `hugo.toml`** so the header
   logo returns to the portal.

### How it works

- **Production:** push to `main` builds Hugo with `baseURL https://packs.nebari.dev/<slug>/`
  and deploys to the pack's Cloudflare Pages project.
- **PR previews:** every pull request builds with `baseURL https://<alias>.<slug>.pages.dev/`
  and deploys to a preview deployment; a bot comments the preview URL on the PR.
- **Fork PRs:** the build and link-check run, but the deploy step is skipped (fork PRs
  cannot read org secrets).
- **Cleanup:** when a PR closes (merged or not), `docs-preview-cleanup.yml` deletes that
  branch's preview deployments. Direct Upload deploys are not tied to the git branch
  lifecycle, so without this previews would linger after the branch is gone.

The edge Worker at `packs.nebari.dev` proxies `/<slug>/*` to `<slug>.pages.dev/*`
transparently. For packs in `tracked-packs.yaml` with `docs_site: true`, the route is
generated automatically. The `building-a-software-pack` route for this template repo is
wired in the dashboard's static extra-routes map.

## License

Apache 2.0 - see [LICENSE](LICENSE).
