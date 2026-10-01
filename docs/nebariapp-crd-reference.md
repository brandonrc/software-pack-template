# NebariApp CRD Reference

Complete field-by-field reference for the NebariApp custom resource.

**API Version:** `reconcilers.nebari.dev/v1`
**Kind:** `NebariApp`
**Source:** [nebari-operator/api/v1/nebariapp_types.go](https://github.com/nebari-dev/nebari-operator/blob/v0.1.1/api/v1/nebariapp_types.go)
**Operator version this doc tracks:** `v0.1.1`. NIC v0.14.0 deploys `v0.1.0-alpha.20`; fields
added after that release are marked *(v0.1.0+)*.

## Full Example

```yaml
apiVersion: reconcilers.nebari.dev/v1
kind: NebariApp
metadata:
  name: my-pack
  namespace: my-pack
spec:
  hostname: my-pack.nebari.example.com
  serviceAccountName: my-pack
  service:
    name: my-pack
    port: 80
  routing:
    routes:
      - pathPrefix: /
        pathType: PathPrefix
    publicRoutes:
      - pathPrefix: /healthz
        pathType: Exact
    tls:
      enabled: true
    annotations:
      argocd.argoproj.io/tracking-id: my-pack
  auth:
    enabled: true
    provider: keycloak
    provisionClient: true
    enforceAtGateway: true
    forwardAccessToken: false
    # redirectURI defaults to /oauth2/callback - omit to use the operator default
    scopes:
      - openid
      - profile
      - email
    # groups is NOT enforced by the gateway in v0.1.1 - see spec.auth below
    groups:
      - admin
  gateway: public
  landingPage:
    enabled: true
    displayName: My Pack
    description: A short description for the landing page card.
    icon: jupyter
    category: Development
    priority: 100
```

## spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `hostname` | string | Yes | - | FQDN where the app will be accessible. Used to generate the HTTPRoute and TLS certificate. Must match pattern `^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$`. |
| `service` | [ServiceReference](#specservice) | Yes | - | The backend Kubernetes Service that receives traffic. |
| `routing` | [RoutingConfig](#specrouting) | No | - | Routing behavior including path rules, TLS, and HTTPRoute annotations. **Omitting `routing` disables operator-managed routing entirely** - the operator skips HTTPRoute creation and cleans up any existing HTTPRoute. TLS is also considered disabled in that case. The NebariApp still reports `Ready=True` (with `RoutingReady=False/RoutingNotConfigured`), so a pack without `routing` deploys cleanly and is unreachable through the gateway. Include at least `routes: [{pathPrefix: /}]`. |
| `auth` | [AuthConfig](#specauth) | No | - | Authentication/authorization configuration. |
| `gateway` | string | No | `"public"` | Which shared Gateway to use. Valid values: `public`, `internal`. |
| `serviceAccountName` | string | No | NebariApp name | Name of the ServiceAccount used by the app's pods. When the operator provisions an OIDC client, it creates a Role and RoleBinding that let this ServiceAccount `get` the OIDC client Secret through the Kubernetes API. This grants access; it does not restrict anyone else. See [who can read the OIDC Secret](#who-can-read-the-oidc-secret). |
| `landingPage` | [LandingPageConfig](#speclandingpage) | No | - | Controls how this service appears on the Nebari landing page. |

## spec.service

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | - | Name of the Kubernetes Service. |
| `port` | int32 | Yes | - | Port number on the Service to route traffic to. Range: 1-65535. |
| `namespace` | string | No | NebariApp's namespace | Namespace of the Service. Allows referencing services in other namespaces for centralized service architectures. The operator has cluster-scoped read permission on Services. |

## spec.routing

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `routes` | [][RouteMatch](#routematch) | No | - | Path-based routing rules. If omitted, all traffic to the hostname is routed to the service. |
| `publicRoutes` | [][RouteMatch](#routematch) | No | - | Paths that bypass OIDC authentication. When `auth.enabled=true` and `auth.enforceAtGateway=true`, these paths are routed via a separate HTTPRoute that is not protected by the SecurityPolicy. Default `pathType` is `Exact` here (vs `PathPrefix` for `routes`) - safer for auth bypass. |
| `tls` | [RoutingTLSConfig](#specroutingtls) | No | - | TLS certificate management configuration. |
| `annotations` | map[string]string | No | - | Additional annotations merged onto the generated HTTPRoute. Useful for tools like ArgoCD that track resources via annotations (e.g. `argocd.argoproj.io/tracking-id`). Operator-managed annotations always take precedence. |

### RouteMatch

Used in both `spec.routing.routes[]` and `spec.routing.publicRoutes[]`.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `pathPrefix` | string | Yes | - | Path to match. Must start with `/`. Examples: `/`, `/api/v1`, `/dashboard`. |
| `pathType` | string | No | `PathPrefix` (in `routes`), `Exact` (in `publicRoutes`) | How the path is matched. Values: `PathPrefix`, `Exact`. |

### spec.routing.tls

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | *bool | No | `true` | Whether to provision a TLS certificate via cert-manager and configure an HTTPS listener on the Gateway. When `false`, only HTTP listeners are used. |
| `secretName` | string | No | - | *(v0.1.0-alpha.20+)* Name of a pre-existing `kubernetes.io/tls` Secret in `envoy-gateway-system` to use for the HTTPS listener instead of a cert-manager Certificate (for wildcard or externally managed certs). When set, the operator creates no Certificate and cleans up any it owns. You create and rotate the Secret. Ignored when `enabled` is `false`. Max 253 chars, DNS-subdomain format. |

## spec.auth

> **Validation rule:** `forwardAccessToken: true` requires `enforceAtGateway: true`.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Whether to enforce OIDC authentication. |
| `provider` | string | No | `"keycloak"` | OIDC provider. Values: `keycloak`, `generic-oidc`. |
| `provisionClient` | *bool | No | `true` | Auto-provision an OIDC client in the provider. Only supported for `keycloak`. The operator creates the client and stores credentials in a Secret named `<name>-oidc-client`. The client ID follows the convention `<namespace>-<nebariapp-name>`. See [auth-flow.md](auth-flow.md#2-kubernetes-secret) for the full secret structure. |
| `enforceAtGateway` | *bool | No | `true` | Create an Envoy Gateway SecurityPolicy for gateway-level auth. When `false`, the operator provisions the client and Secret but does NOT create a SecurityPolicy - the app handles OAuth natively. See [auth-flow.md](auth-flow.md#app-native-oauth) for wiring guidance. |
| `forwardAccessToken` | *bool | No | `false` | When `enforceAtGateway: true`, forward the user's OAuth access token to the upstream service via the `Authorization: Bearer <token>` header. Use when the app needs to read the JWT itself (e.g., to inspect the `groups` claim for per-user authorization). Without this, the gateway only stores the token in an encrypted session cookie that the backend cannot decode. |
| `denyRedirect` | [][DenyRedirectHeader](#denyredirectheader) | No | - | Headers that, when matched, prevent the OIDC filter from redirecting to the IdP and instead return 401. Helps avoid PKCE race conditions when SPAs fire multiple parallel requests on page load. Only applies when `enforceAtGateway: true`. |
| `redirectURI` | string | No | `"/oauth2/callback"` | OAuth2 callback path. The full URL is `https://<hostname><redirectURI>`. |
| `clientSecretRef` | string | No | - | **Accepted by the API but ignored by operator v0.1.1.** The operator always reads and writes the Secret named `<nebariapp-name>-oidc-client` (keys `client-id`, `client-secret`, `issuer-url`). If you manage credentials yourself (`provisionClient: false`), create the Secret under that name. |
| `scopes` | []string | No | `["openid", "profile", "email"]` | OIDC scopes to request during authentication. |
| `groups` | []string | No | - | **Not enforced in v0.1.1.** The operator creates these groups in Keycloak and publishes them as `status.serviceDiscovery.requiredGroups` for the landing page, but the SecurityPolicy it generates contains no authorization rule, so any user who can log in to the realm reaches the app ([nebari-operator#153](https://github.com/nebari-dev/nebari-operator/issues/153)). To restrict access by group today, verify the token in your app and check its `groups` claim (see [Authentication Flow](auth-flow.md#reading-user-identity-in-your-app)). |
| `issuerURL` | string | No | - | OIDC issuer URL. Required when `provider=generic-oidc`, ignored for `keycloak`. Example: `https://accounts.google.com`. |
| `spaClient` | [SPAClientConfig](#specauthspaclient) | No | - | Provisions a public Keycloak client for browser-based PKCE flows (e.g., React apps using `keycloak-js`). Distinct from the confidential client used by gateway-enforced auth. |
| `deviceFlowClient` | [DeviceFlowClientConfig](#specauthdeviceflowclient) | No | - | Provisions a public Keycloak client for the OAuth2 Device Authorization Grant (RFC 8628), for CLI/native apps. |
| `keycloakConfig` | [KeycloakClientConfig](#specauthkeycloakconfig) | No | - | Keycloak-specific configuration: realm groups (with optional member assignments) and client-level protocol mappers. Only applied when `provider=keycloak` and `provisionClient=true`. |
| `tokenExchange` | [TokenExchangeConfig](#specauthtokenexchange) | No | - | OAuth 2.0 Token Exchange (RFC 8693). When enabled, other NebariApp clients in the same Keycloak realm can exchange their access tokens for tokens with this client's audience. Requires `KC_FEATURES=token-exchange` on the Keycloak server. |

### DenyRedirectHeader

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | - | Header name to match against. |
| `value` | string | Yes | - | Header value to match. |
| `type` | string | No | `Exact` | Match type. Values: `Exact`, `Prefix`, `Suffix`, `RegularExpression`. |

### spec.auth.spaClient

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Provision a public OIDC client for SPA use (PKCE, no client secret). The public client ID is written to the OIDC Secret as `spa-client-id`. |
| `clientId` | string | No | `<namespace>-<name>-spa` | Override the generated client ID. |

### spec.auth.deviceFlowClient

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Provision a public OIDC client configured for the Device Authorization Grant. The client ID is written to the OIDC Secret as `device-client-id`. |

### spec.auth.keycloakConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `groups` | [][KeycloakGroup](#keycloakgroup) | No | - | Groups to ensure exist in the realm, with optional user membership assignments. |
| `protocolMappers` | [][KeycloakProtocolMapperConfig](#keycloakprotocolmapperconfig) | No | - | Client-level protocol mappers applied directly to the OIDC client. When specified, the operator's default mappers (e.g., group-membership) are not auto-created. |

#### KeycloakGroup

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | - | Group name to create in Keycloak. |
| `members` | []string | No | - | Keycloak usernames to add to the group. Membership sync is **additive-only** - users not in this list are not removed. |

#### KeycloakProtocolMapperConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | - | Protocol mapper name. |
| `protocolMapper` | string | Yes | - | Mapper type identifier (e.g., `oidc-group-membership-mapper`). |
| `config` | map[string]string | No | - | Mapper configuration as arbitrary key-value pairs. Keys/values are mapper-type specific. |

### spec.auth.tokenExchange

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Enable authorization services on the Keycloak client and create policies allowing other NebariApp clients in the same realm to exchange tokens for this client's audience. |

## spec.landingPage

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Whether this service appears on the Nebari landing page. Set to `true` to opt in. |
| `displayName` | string | No | - | Human-readable name on the landing page. Max 64 chars. Set it whenever `enabled` is `true`; the operator does not reject a missing value. |
| `description` | string | No | - | Supplementary text on the service card. Max 256 chars. |
| `icon` | string | No | - | Icon identifier or URL, shown in both light and dark mode unless `iconLight`/`iconDark` are set. Built-in icons: `jupyter`, `grafana`, `prometheus`, `keycloak`, `argocd`, `kubernetes`. |
| `iconLight` | string | No | - | *(v0.1.0+)* URL of the icon shown when the UI is in light mode. Takes precedence over `icon`. |
| `iconDark` | string | No | - | *(v0.1.0+)* URL of the icon shown when the UI is in dark mode. Takes precedence over `icon`. |
| `category` | string | No | - | Group services together. Common categories: `Development`, `Monitoring`, `Platform`, `Data Science`. |
| `priority` | *int | No | `100` | Sort order within a category (lower = higher priority). Range: 0-1000. |
| `externalUrl` | string | No | `https://<hostname>` (`http://` when `routing.tls.enabled: false`) | Override the default URL. |
| `healthCheck` | [HealthCheckConfig](#speclandingpagehealthcheck) | No | - | Health-status monitoring for the service card. |

### spec.landingPage.healthCheck

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | No | `false` | Whether health checks are performed. |
| `path` | string | No | `/health` | HTTP path to check. |
| `port` | *int32 | No | `spec.service.port` | Port to use for health checks. Range: 1-65535. |
| `intervalSeconds` | *int | No | `30` | Health check interval. Range: 10-300. |
| `timeoutSeconds` | *int | No | `5` | Per-check request timeout. Range: 1-30. |

## Status

The operator writes status conditions and several status fields for downstream consumers (the webapi watcher, ArgoCD, etc.).

### Conditions

| Condition | Description |
|-----------|-------------|
| `RoutingReady` | HTTPRoute has been created and the Gateway is routing traffic. |
| `TLSReady` | TLS certificate is provisioned and the HTTPS listener is configured. |
| `AuthReady` | SecurityPolicy is created and the OIDC client is available. `False` with reason `AuthDisabled` when auth is off. |
| `Ready` | Set from the core checks (namespace label, Service, validation) and from hard reconcile failures. It does **not** wait for the other three: a NebariApp with `RoutingReady=False/RoutingNotConfigured` or `TLSReady=False/TLSDisabled` still reports `Ready=True`. Check the specific conditions you depend on. |

### Condition Reasons

These are the reasons the v0.1.1 controllers set, grouped by condition.

| Condition | Reason | Meaning |
|-----------|--------|---------|
| `Ready` | `Reconciling` | Reconciliation is in progress. |
| `Ready` | `ValidationSuccess` | Core validation passed. |
| `Ready` | `ReconcileSuccess` | Reconciliation completed. Does not imply routing, TLS or auth are ready. |
| `Ready` | `Failed` | A reconcile step failed; the message names it. |
| `Ready` | `NamespaceNotOptedIn` | Namespace is missing the `nebari.dev/managed=true` label. |
| `Ready` | `ServiceNotFound` | The referenced Service does not exist. |
| `Ready` | `ResourceNameTooLong` | A generated resource name would exceed Kubernetes limits. Shorten the NebariApp name. |
| `RoutingReady` | `HTTPRouteCreated` / `HTTPRouteReady` | The HTTPRoute exists and is configured. |
| `RoutingReady` | `RoutingNotConfigured` | `spec.routing` is unset, so no HTTPRoute is created. |
| `RoutingReady` | `BuildFailed` / `CreationFailed` / `UpdateFailed` | The HTTPRoute could not be built, created or updated. |
| `TLSReady` | `TLSConfigured` | Certificate and HTTPS listener are in place. |
| `TLSReady` | `TLSDisabled` | TLS is off: `routing.tls.enabled: false`, or `spec.routing` is unset. |
| `TLSReady` | `ClusterIssuerNotConfigured` | The operator has no cert-manager ClusterIssuer configured and no `tls.secretName` was given. |
| `TLSReady` | `CertificateNotReady` | The cert-manager Certificate exists but is not ready yet. |
| `TLSReady` | `CertificateFailed` / `CertificateCheckFailed` / `CertificateCleanupFailed` | Creating, checking or removing the Certificate failed. |
| `TLSReady` | `GatewayListenerFailed` | The per-app HTTPS listener could not be added to the Gateway. |
| `TLSReady` | `GatewayListenerConflict` | Another NebariApp already owns a listener for this hostname. |
| `TLSReady` | `UserProvidedSecretReady` | *(v0.1.0-alpha.20+)* The `tls.secretName` Secret exists and has type `kubernetes.io/tls`. |
| `TLSReady` | `UserProvidedSecretNotFound` / `UserProvidedSecretInvalidType` | *(v0.1.0-alpha.20+)* The `tls.secretName` Secret is missing or is not `kubernetes.io/tls`. |
| `TLSReady` | `UserProvidedSecretCheckFailed` | *(v0.1.0-alpha.20+)* The Secret could not be checked (transient API or RBAC error). |
| `AuthReady` | `AuthConfigured` | OIDC client and SecurityPolicy are configured. |
| `AuthReady` | `AuthDisabled` | `auth.enabled` is `false`. |
| `AuthReady` | `InvalidProvider` / `ValidationFailed` | The auth configuration is invalid; the message says why. |
| `AuthReady` | `ProvisioningFailed` / `ProvisioningNotSupported` | The OIDC client could not be provisioned, or the provider does not support provisioning. |
| `AuthReady` | `RBACFailed` | The Role/RoleBinding for the OIDC Secret could not be created. |
| `AuthReady` | `SecurityPolicyFailed` / `SecurityPolicyCleanupFailed` | Creating or removing the SecurityPolicy failed. |
| `AuthReady` | `TokenExchangeFailed` | Token exchange policies could not be configured. |

The API also defines `Available`, `SecretNotFound` and `GatewayNotFound`, but v0.1.1 never sets them.

### Status Fields

| Field | Type | Description |
|-------|------|-------------|
| `observedGeneration` | int64 | Most recent `metadata.generation` observed by the controller. |
| `hostname` | string | Mirror of `spec.hostname` for easy reference. |
| `gatewayRef` | object `{name, namespace}` | Defined in the API but not written by v0.1.1. |
| `clientSecretRef` | object `{name, namespace}` | Defined in the API but not written by v0.1.1. The Secret is always `<name>-oidc-client`. |
| `authConfigHash` | string | SHA-256 hash of the last successfully provisioned OIDC client config. Used to skip re-provisioning when the spec is unchanged. To force re-provisioning, set the `nebari.dev/force-reprovision` annotation; the operator removes it once the forced re-provision completes. |
| `serviceDiscovery` | object | URL-resolved view of `spec.landingPage` for the webapi/landing-page watcher. Includes `enabled`, `displayName`, `description`, `url`, `icon`, `iconLight`, `iconDark`, `category`, `priority`, `visibility`, `requiredGroups`. `requiredGroups` is informational; see `spec.auth.groups`. |

## Namespace Opt-In

The namespace containing the NebariApp must be labeled for the operator to process it:

```bash
kubectl label namespace my-pack nebari.dev/managed=true
```

Without this label, the NebariApp will show `NamespaceNotOptedIn` and no resources
will be created.

When ArgoCD creates the namespace (`CreateNamespace=true`), have it apply the label too,
so nobody has to run `kubectl` by hand:

```yaml
spec:
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
    managedNamespaceMetadata:
      labels:
        nebari.dev/managed: "true"
```

ArgoCD only applies `managedNamespaceMetadata` to a namespace that the same Application
creates. If the namespace already exists, label it yourself.

## Who can read the OIDC Secret

When `provisionClient` is true, the operator writes `<name>-oidc-client` and creates a Role
(`<name>-oidc-secret-reader`) that lets `spec.serviceAccountName` `get` that Secret through the
Kubernetes API. That Role adds access for one ServiceAccount. It does not remove access from
anyone else. Kubernetes RBAC is additive, so these can also read the Secret:

- anyone who already has `get` on Secrets in the namespace, such as namespace admins
- any pod in the namespace, under any ServiceAccount, that mounts the Secret as an env var or
  volume (the kubelet fetches it, not the pod's ServiceAccount), and therefore anyone who can
  create pods there
- the operator, which has cluster-wide Secret access, and Envoy Gateway, which reads the
  client secret to run the OIDC filter

Treat the namespace as the security boundary for these credentials.

## Deployment Patterns

The NebariApp resource can be included in your pack using any deployment method.

### Plain YAML

The NebariApp is just another manifest file alongside your Deployment and Service:

```yaml
# nebariapp.yaml
apiVersion: reconcilers.nebari.dev/v1
kind: NebariApp
metadata:
  name: my-pack
spec:
  hostname: my-pack.nebari.example.com
  service:
    name: my-pack
    port: 80
  routing:
    routes:
      - pathPrefix: /
    tls:
      enabled: true
```

When deploying standalone (without Nebari), skip this file in your `kubectl apply`.

### Kustomize

Include the NebariApp in your base `kustomization.yaml` and use overlays to
patch environment-specific values like `hostname` and `auth`:

```yaml
# overlays/production/nebariapp-patch.yaml
apiVersion: reconcilers.nebari.dev/v1
kind: NebariApp
metadata:
  name: my-pack
spec:
  hostname: my-pack.nebari.example.com
  auth:
    enabled: true
```

A strategic-merge patch only changes the fields it lists, so `routing` from the base is kept.

### Helm

Charts render the NebariApp through the shared `nebari-app.nebariApp` template
provided by the `nebari-app` chart, instead of hand-writing the manifest.

Add the dependency in `Chart.yaml`, then run `helm dependency build` to fetch it:

```yaml
dependencies:
  - name: nebari-app
    repository: oci://quay.io/nebari/charts
    version: ">=0.1.1"
```

Set any NebariApp `spec` field under `nebariapp:` in `values.yaml`. Everything
under `nebariapp:` (except `enabled`) is passed through to the NebariApp spec,
so all fields documented above can be set here. Each `{{ ... }}` value is rendered
with the chart context and must produce valid JSON, so a template that renders a string
ends with `| toJson`. Numbers such as the port below don't need it:

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
        pathType: PathPrefix
    tls:
      enabled: true
  auth:
    enabled: false
    provider: keycloak
    provisionClient: true
    scopes:
      - openid
      - profile
      - email
  gateway: public
```

Render the NebariApp in `templates/nebariapp.yaml`. The `if` makes it optional,
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

`"tplCtx" .` passes the chart context into the template, so the `{{ ... }}`
values in `values.yaml` are rendered with access to `.Values`, `.Release`, and
the chart's named templates.
