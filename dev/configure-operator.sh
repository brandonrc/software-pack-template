#!/usr/bin/env bash
# Configures the nebari-operator in the kind dev cluster the way NIC configures
# it on a real Nebari cluster (see nebari-infrastructure-core
# pkg/argocd/templates/manifests/nebari-operator/deployment-patch.yaml), and
# exposes Keycloak through the Gateway at keycloak.nebari.local.
#
# The release install.yaml leaves these unset. Without them:
#   - the in-cluster issuer defaults to port 8080 with no /auth path, Envoy
#     Gateway rejects every SecurityPolicy, and auth-enabled apps return 500
#   - the browser is sent to in-cluster Keycloak URLs it cannot reach
#   - no per-app Certificate is issued (TLSReady=False/ClusterIssuerNotConfigured)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

kubectl apply -f "${SCRIPT_DIR}/keycloak-route.yaml"

kubectl set env -n nebari-operator-system deployment/nebari-operator-controller-manager \
  KEYCLOAK_ISSUER_SERVICE_PORT=80 \
  KEYCLOAK_ISSUER_CONTEXT_PATH=/auth \
  KEYCLOAK_EXTERNAL_URL=https://keycloak.nebari.local/auth \
  TLS_CLUSTER_ISSUER_NAME=nebari-ca-issuer

kubectl rollout status -n nebari-operator-system \
  deployment/nebari-operator-controller-manager --timeout=120s
