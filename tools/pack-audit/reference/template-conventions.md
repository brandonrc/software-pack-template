# What a healthy Nebari software pack looks like

Distilled on 2026-10-09 from `nebari-dev/software-pack-template` (the contract)
and the first-party packs that follow it (llm-serving, mlflow, superset, harbor,
langfuse, data-science, lgtm). Use this as the mental model when judging a
pack; the scored rubric is `checklist.yaml`.

## The one non-negotiable

A pack is a Kubernetes deployment that carries a `NebariApp`
(`reconcilers.nebari.dev/v1`). The nebari-operator reads it and creates the
HTTPRoute on the shared Envoy Gateway, the cert-manager Certificate, and (if
`auth.enabled`) the Keycloak client plus the Envoy SecurityPolicy. Anything
that fronts itself with its own Ingress, gateway, or identity provider is not
integrated, however good the chart is.

Minimum useful spec (operator v0.1.1):

```yaml
spec:
  hostname: app.nebari.example.com        # required, DNS-label pattern
  service: {name: <svc>, port: <int>}     # must match a rendered Service
  routing:                                # REQUIRED in practice: omitted => no HTTPRoute
    routes: [{pathPrefix: /, pathType: PathPrefix}]
    tls: {enabled: true}
  auth:
    enabled: true
    provider: keycloak
    provisionClient: true
    enforceAtGateway: true                 # false only for app-native OAuth (Harbor, Grafana)
    scopes: [openid, profile, email, groups]   # include groups whenever auth.groups is set
    groups: []                             # optional allow-list; needs the groups scope above
  gateway: public                          # or internal
  landingPage: {enabled: true, displayName: ...}
```

The namespace must carry `nebari.dev/managed=true` (ArgoCD
`managedNamespaceMetadata` is the usual way).

## Repository shape

```
<pack>/
  chart/                      # or Chart.yaml at the root; one pack chart
    Chart.yaml                # apiVersion v2, semver version, appVersion = upstream app
    values.yaml               # nebariapp: {enabled, hostname, service, routing, auth, ...}
    values.schema.json        # optional but common
    templates/
      nebariapp.yaml          # gated by .Values.nebariapp.enabled
      _helpers.tpl, NOTES.txt
  examples/
    argocd-application.yaml   # real repoURL, valuesObject with nebariapp
    nebari-values.yaml        # full Nebari deployment
    standalone-values.yaml    # nebariapp.enabled=false (if standalone-supported)
  dev/Makefile                # kind cluster with operator+gateway+keycloak
  docs/                       # Astro site on @nebari/starlight, base /<slug>/
  .github/workflows/
    lint.yaml                 # helm lint; helm template both modes; kubeconform; check-jsonschema pack-metadata.yaml
    test.yaml                 # kind install standalone, port-forward health check
    test-integration.yaml     # operator installed, NebariApp Ready, SecurityPolicy present
    release.yaml              # pack-release.yaml@v1 (first-party) or equivalent publish
    build-images.yaml         # pack-build-image.yaml@v1 when there are first-party images
    docs.yml, docs-preview-cleanup.yml
  pack-metadata.yaml          # name, display_name, level, owner, deprecated, nebariapp_integration, scope
  CODEOWNERS
  README.md                   # what / who / deploy command / prerequisites / known limitations / troubleshooting / link to docs
  SECURITY.md, LICENSE, CHANGELOG.md, .editorconfig, .gitignore
```

Two accepted ways to render the NebariApp:

- the `nebari-app` library chart (`oci://quay.io/nebari/charts`, `>=0.1.1`) via
  `include "nebari-app.nebariApp"` with `tplCtx`, which lets `values.yaml`
  carry `{{ }}` expressions ending in `| toJson`;
- a hand-written `templates/nebariapp.yaml` (harbor, langfuse, and most packs built outside the org).

Both are fine. What matters is the `enabled` gate, explicit `routing`, and a
`service` reference that resolves.

## Wrapping upstream software

Most packs wrap an upstream chart: add it to `Chart.yaml` `dependencies`,
override its values under its key, and point the NebariApp at its Service.
Vendoring a copy of the upstream chart under `charts/` is tolerated but is a
maintenance liability and a sign the pack has not been shaped yet.

## Values conventions

- `nebariapp.enabled: false` by default so the chart installs standalone.
- `nebariapp.hostname` is required when enabled (`required` / `fail`).
- Secrets: `existingSecret` names, `required` for must-set values, or
  `lookup`-based generation guarded for GitOps. No literal defaults.
- Images: immutable tag or digest; first-party images get `sha-<short>` tags
  pinned by the release workflow.
- Every `nebariapp.*` key is commented in `values.yaml` or tabled in the docs.

## CI conventions

- Lint on every push/PR: `helm lint`, `helm template` with the NebariApp on
  and off, `kubeconform -strict -ignore-missing-schemas`,
  `check-jsonschema` on `pack-metadata.yaml`.
- Standalone test on kind with `--wait` and an HTTP health check.
- Integration test with the operator (pinned release or
  `action-nebari-sandbox`) waiting for `NebariApp` `Ready`.
- Release on `Chart.yaml` version bump or `v*` tag; chart packaged and
  published; images pinned to the release sha.

## Maturity, in practice

- **Experimental**: repo shaped like the template, owner known, metadata
  present, README says what it is.
- **Alpha**: installs on a NIC dev cluster from the README, NebariApp goes
  Ready, prerequisites and known limitations written down, one example
  values file that works with only a hostname change.
- **Beta**: lint/template/kubeconform in CI, probes, non-root, pinned images,
  no literal secrets, auth and troubleshooting docs, Nebari + standalone
  examples, ArgoCD example, a published 0.x release, `product_owner` set.
- **GA**: integration test against the NIC stack, hardened securityContext,
  NetworkPolicy, sizing and upgrade docs, CHANGELOG, docs site, 1.0.0,
  install-then-upgrade smoke test.

## Things that look like integration but are not

- An `Ingress` (ALB, Traefik, nginx) alongside or instead of the NebariApp.
- A bundled Keycloak, Ory, Istio, or Envoy: the platform already provides
  identity and the gateway.
- `routing` omitted from the NebariApp.
- `auth.enforceAtGateway: false` without docs on how the app uses the
  operator-provisioned OIDC client Secret.
- `auth.groups` set without `groups` in `auth.scopes`: the operator attaches
  the Keycloak groups scope only when requested, so the token has no groups
  claim and the group gate rejects everyone (seen live on a NIC cluster).
- Placeholders (`<registry>`, `REPLACE_`, `CHANGEME`) that `helm install`
  accepts silently.
