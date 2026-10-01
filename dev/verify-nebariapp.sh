#!/usr/bin/env bash
# Checks that a NebariApp is actually serving traffic, not just reporting Ready.
#
#   ./verify-nebariapp.sh <nebariapp-name> <hostname> [--auth] [--namespace <ns>]
#
# Ready=True alone proves little: a NebariApp with no spec.routing is Ready but
# has no HTTPRoute and returns 404. This waits for the routing, TLS and (with
# --auth) auth conditions, checks the generated resources exist, and sends a
# request through the Gateway: 200 without auth, a redirect to Keycloak with it.
set -euo pipefail

NAME=${1:?usage: verify-nebariapp.sh <name> <hostname> [--auth] [--namespace <ns>]}
HOST=${2:?usage: verify-nebariapp.sh <name> <hostname> [--auth] [--namespace <ns>]}
shift 2
AUTH=false
NS=default
while [ $# -gt 0 ]; do
  case "$1" in
    --auth) AUTH=true ;;
    --namespace) NS=$2; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

wait_cond() {
  kubectl wait -n "$NS" --for=condition="$1" "nebariapp/$NAME" --timeout=180s
}

wait_cond Ready
wait_cond RoutingReady
wait_cond TLSReady
kubectl get -n "$NS" httproute "$NAME-route" >/dev/null

if $AUTH; then
  wait_cond AuthReady
  kubectl get -n "$NS" securitypolicy "$NAME-security" >/dev/null
  expect="302 https://keycloak.nebari.local/"
else
  expect="200 "
fi

GW=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=nebari-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

# Envoy Gateway needs a few seconds to push new listeners and policies.
for _ in $(seq 1 30); do
  got=$(curl -sk -o /dev/null -w '%{http_code} %{redirect_url}' \
    --resolve "$HOST:443:$GW" "https://$HOST/" || true)
  case "$got" in
    "$expect"*) echo "OK: $NAME serves https://$HOST/ ($got)"; exit 0 ;;
  esac
  sleep 4
done

echo "FAIL: https://$HOST/ returned '$got', expected '$expect...'" >&2
kubectl get -n "$NS" nebariapp "$NAME" \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status}/{.reason}: {.message}{"\n"}{end}' >&2
exit 1
