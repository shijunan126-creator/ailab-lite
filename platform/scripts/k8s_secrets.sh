#!/usr/bin/env bash
# Independent caller secrets; recipients get only SHA-256 verifiers of other callers.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
NS=platform

apply_secret() {
  local name=$1; shift
  if [ -n "${DRY_RUN:-}" ]; then
    echo "[k8s_secrets] would reconcile $name (values redacted)"
  else
    $KUBECTL -n "$NS" create secret generic "$name" "$@" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
  fi
}

case "${1:-create}" in
  env)
    for key in AILAB_ADMIN_PASSWORD AILAB_DEV_TOKEN; do
      value=$($KUBECTL -n "$NS" get secret platform-secrets -o "jsonpath={.data.$key}" | base64 -d)
      [ -n "$value" ] && printf 'export %s=%q\n' "$key" "$value"
    done
    value=$($KUBECTL -n "$NS" get secret platform-ops-identity -o 'jsonpath={.data.AILAB_SERVICE_CREDENTIAL}' | base64 -d)
    printf 'export AILAB_SERVICE_NAME=ops\nexport AILAB_SERVICE_CREDENTIAL=%q\n' "$value"
    ;;
  create)
    if [ -z "${DRY_RUN:-}" ]; then
      $KUBECTL get ns "$NS" >/dev/null 2>&1 || $KUBECTL create ns "$NS"
    fi
    if [ -n "${DRY_RUN:-}" ] || ! $KUBECTL -n "$NS" get secret platform-secrets >/dev/null 2>&1; then
      args=(--from-literal=AILAB_ADMIN_PASSWORD="$(openssl rand -hex 10)"
            --from-literal=AILAB_DEV_TOKEN="$(openssl rand -hex 24)"
            --from-literal=AILAB_OSB_API_KEY="${AILAB_OSB_API_KEY:-dev-osb-key}")
      for name in $(compgen -e | grep '^AILAB_KEY_' || true); do args+=(--from-literal="$name=${!name}"); done
      apply_secret platform-secrets "${args[@]}"
    fi
    verifiers=()
    for service in api agent broker worker ops; do
      token=''
      if [ -z "${DRY_RUN:-}" ]; then
        token=$($KUBECTL -n "$NS" get secret "platform-$service-identity" --ignore-not-found -o 'jsonpath={.data.AILAB_SERVICE_CREDENTIAL}' | base64 -d)
      fi
      if [ -z "$token" ]; then
        token=$(openssl rand -hex 32)
        apply_secret "platform-$service-identity" --from-literal=AILAB_SERVICE_CREDENTIAL="$token"
      fi
      digest=$(printf %s "$token" | openssl dgst -sha256 | awk '{print $NF}')
      verifiers+=("\"$service\":\"sha256:$digest\"")
    done
    # Shared only by the Agent and the crawler it calls; kept across runs like the service identities.
    if [ -n "${DRY_RUN:-}" ] || ! $KUBECTL -n "$NS" get secret platform-crawler-token >/dev/null 2>&1; then
      apply_secret platform-crawler-token --from-literal=AILAB_CRAWLER_TOKEN="$(openssl rand -hex 32)"
    fi
    registry=$(IFS=,; printf '{%s}' "${verifiers[*]}")
    apply_secret platform-service-verifiers --from-literal=AILAB_SERVICE_CREDENTIALS="$registry"
    # API needs keys to advertise available models; Agent needs keys to call them. No other pod receives them.
    if [ -z "${DRY_RUN:-}" ]; then
      $KUBECTL -n "$NS" get secret platform-secrets -o json | python3 -c '
import json,sys
s=json.load(sys.stdin)
print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":"platform-model-secrets","namespace":"platform"},"data":{k:v for k,v in s["data"].items() if k.startswith("AILAB_KEY_")}}))
' | $KUBECTL apply -f - >/dev/null
    fi
    echo '[k8s_secrets] service identities reconciled; existing credentials preserved'
    ;;
  *) echo "usage: $0 [create|env]"; exit 2 ;;
esac
