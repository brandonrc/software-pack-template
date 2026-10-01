---
title: Build your own pack
---

If a workload you'd like to run on Nebari isn't already in the catalog, you can package it yourself.

## What's in a pack?

A pack is a Kubernetes application bundled with a `NebariApp` custom resource. The Nebari Operator reads the `NebariApp` to wire up routing, TLS, and authentication for your app.

If your app already runs on Kubernetes - via Helm, Kustomize, or plain YAML - adding a `NebariApp` resource is all it takes to make it a pack.

## Start from the template

The easiest way to create a pack is the [Software Pack template](https://github.com/nebari-dev/software-pack-template).

1. Click **Use this template** on the template repository.
2. Clone your new repo.
3. Pick the example closest to your application.
4. Follow the instructions in the README to deploy your pack to a Nebari cluster.

The template ships five examples of increasing complexity:

| Example | Best for |
|---------|----------|
| `examples/vanilla-yaml/` | Plain `kubectl apply`, no tooling |
| `examples/kustomize-nginx/` | Per-environment overlays |
| `examples/basic-nginx/` | Simplest Helm chart |
| `examples/auth-fastapi/` | Custom app that reads auth tokens |
| `examples/wrap-existing-chart/` | Wrapping an existing upstream Helm chart |

## Deploy via ArgoCD

To deploy a pack, commit an ArgoCD Application to your gitops repo. This is a small YAML file that tells ArgoCD which pack to deploy and how to configure it.

ArgoCD then:

1. Reads the Application.
2. Pulls in the pack from its repository.
3. Applies the Application's configuration values to the pack.
4. Applies the resulting resources to the cluster.

This GitOps approach means the pack configuration is version-controlled alongside everything else in your infrastructure.

On a cluster built with Nebari Infrastructure Core (NIC), the Application needs two things the generic ArgoCD docs won't tell you:

- **`project: nebari-apps`.** NIC creates this AppProject for packs. Its built-in `default` project is locked to deny-all, so an Application left on `default` never syncs.
- **The namespace opt-in label.** The operator ignores NebariApps in namespaces without `nebari.dev/managed=true` (status `NamespaceNotOptedIn`). When ArgoCD creates the namespace, have it apply the label with `managedNamespaceMetadata`. ArgoCD only does this for a namespace the same Application creates, so label a pre-existing namespace yourself.

A Helm pack from a git repo looks like this:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-pack
  namespace: argocd
spec:
  project: nebari-apps
  source:
    repoURL: https://github.com/my-org/my-pack.git
    targetRevision: main
    path: chart
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
    managedNamespaceMetadata:
      labels:
        nebari.dev/managed: "true"
```

Once it syncs, check that the pack is actually reachable, not just `Ready`:

```bash
kubectl get nebariapp -n my-pack my-pack \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status}/{.reason}{"\n"}{end}'
```

`RoutingReady` must be `True`. `Ready=True` alone is not enough: a NebariApp without `spec.routing` reports `Ready` and serves nothing.

## Private and organizational packs

Packs do not need to be public. Put yours in a private GitHub repo, an internal Git host, or a private chart registry. The `nebari-apps` project accepts any source, so nothing changes in the Application itself, and the pack won't appear in the public catalog.

ArgoCD does need credentials to read a private source, and NIC does not create them. Add a repository Secret in the `argocd` namespace (see the [ArgoCD repository docs](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#repositories)):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: my-pack-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: https://github.com/my-org/my-pack.git
  username: git            # any non-empty value works with a GitHub token
  password: <read-only token>
```

For a private OCI chart registry, use `type: helm`, `enableOCI: "true"`, and the registry host and path without the `oci://` prefix as `url`. Keep the token out of the gitops repo in plain text; create the Secret with your secrets tooling (Sealed Secrets, External Secrets, or `kubectl` directly).

## Going deeper

- [NebariApp CRD reference](/nebariapp-crd-reference/) - every field explained
- [Authentication flow](/auth-flow/) - how OIDC works end-to-end, including reading the IdToken in your app
- [Release readiness](/release-readiness/) - maturity levels and the promotion checklist for official packs
