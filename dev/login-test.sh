#!/usr/bin/env bash
# Drives the browser half of the OIDC login with curl, against the local kind
# cluster, and prints what the app shows afterwards.
#
#   ./login-test.sh <app-hostname> [username] [password]
#
# Requests are pinned to the Gateway's LoadBalancer IP with --resolve, so this
# works without /etc/hosts entries. -k is used because the dev CA is self-signed.
set -euo pipefail

APP_HOST=${1:?usage: login-test.sh <app-hostname> [username] [password]}
USERNAME=${2:-admin}
PASSWORD=${3:-nebari-admin}
KC_HOST=keycloak.nebari.local

GW=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=nebari-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT
CURL=(curl -sk -b "$JAR" -c "$JAR" --resolve "$APP_HOST:443:$GW" --resolve "$KC_HOST:443:$GW")

# 1. App redirects to Keycloak; follow it to the login form.
form=$("${CURL[@]}" -L "https://$APP_HOST/")
action=$(printf '%s' "$form" | grep -o 'action="[^"]*"' | head -1 | sed 's/^action="//; s/"$//; s/&amp;/\&/g')
if [ -z "$action" ]; then
  echo "FAIL: no Keycloak login form (is the SecurityPolicy accepted?)" >&2
  exit 1
fi

# 2. Submit credentials. Keycloak redirects to /oauth2/callback, Envoy sets the
#    session cookies and redirects back to the app.
page=$("${CURL[@]}" -L --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" "$action")

if printf '%s' "$page" | grep -q "Authenticated User"; then
  echo "OK: logged in; app shows verified identity for '$USERNAME'"
else
  echo "FAIL: app did not show a verified identity" >&2
  printf '%s' "$page" | tr -s ' \n' ' ' | grep -o '<h2>Not Authenticated</h2> <p>[^<]*' | sed 's/<[^>]*>//g' >&2
  exit 1
fi
