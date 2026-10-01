# Example 3: Basic Helm Pack (Nginx)

The simplest possible Helm-based Nebari Software Pack. Deploys a stock nginx
container with optional Nebari platform integration via the NebariApp CRD.

## What This Example Shows

- Minimum viable Helm chart structure for a Nebari pack
- Rendering the NebariApp custom resource with the official
  [`nebari-app` library chart](https://github.com/nebari-dev/nebari-operator/tree/main/charts/nebari-app)
- How to toggle Nebari integration on/off with `nebariapp.enabled`
- Health probes, service, and deployment boilerplate

## Deploying to Nebari

### ArgoCD Application (recommended)

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
    path: examples/basic-nginx/chart
    helm:
      valuesObject:
        nebariapp:
          enabled: true
          hostname: my-pack.nebari.example.com
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

The nebari-operator will automatically create:
- An HTTPRoute directing traffic from `my-pack.nebari.example.com` to your service
- A TLS certificate via cert-manager

To enable authentication, add `auth` to the values:

```yaml
    helm:
      valuesObject:
        nebariapp:
          enabled: true
          hostname: my-pack.nebari.example.com
          auth:
            enabled: true
```

This additionally creates:
- A Keycloak OIDC client (auto-provisioned)
- An Envoy Gateway SecurityPolicy that requires login before accessing the app

### Helm install

```bash
# Fetch the nebari-app dependency
helm dependency build ./chart/

# Deploy on Nebari
helm install my-pack ./chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com

# Deploy on Nebari with auth
helm install my-pack ./chart/ \
  --set nebariapp.enabled=true \
  --set nebariapp.hostname=my-pack.nebari.example.com \
  --set nebariapp.auth.enabled=true
```

## Local development (standalone, no Nebari)

```bash
helm dependency build ./chart/
helm install test-basic ./chart/

# Access via port-forward
kubectl port-forward svc/test-basic-my-pack 8080:80
# Open http://localhost:8080
```

## Files

| File | Purpose |
|------|---------|
| `chart/Chart.yaml` | Helm chart metadata with the nebari-app dependency |
| `chart/values.yaml` | Default configuration values, including the NebariApp spec under `nebariapp:` |
| `chart/templates/_helpers.tpl` | Name, label, and selector helpers |
| `chart/templates/nebariapp.yaml` | Renders the NebariApp via nebari-app (conditional on `nebariapp.enabled`) |
| `chart/templates/deployment.yaml` | Kubernetes Deployment for nginx |
| `chart/templates/service.yaml` | ClusterIP Service |
| `chart/templates/NOTES.txt` | Post-install instructions |

## Customizing

To use this as a starting point for your own pack:

1. Replace `my-pack` with your pack name in all files
2. Replace the nginx image with your application image
3. Update the container port and health probe paths as needed
4. Set `nebariapp.hostname` to your desired domain

See the [main README](../../README.md) for the full customization guide.
