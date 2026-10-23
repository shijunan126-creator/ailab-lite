set -euo pipefail
step() { echo "$*"; }
ok() { echo "PASS $*"; }
die() { echo "FAIL $*" >&2; exit 1; }
KUBECTL=/tmp/ailab-real-verify-20261023/kubectl
k() { "$KUBECTL" -n platform "$@"; }
dev_token() { k get secret platform-secrets -o jsonpath='{.data.AILAB_DEV_TOKEN}' | base64 -d; }
API_URL=http://127.0.0.1:30080 WITH_PORTAL=0 SMOKE_REAL=0 DRY=0 DEFAULT_MODEL=ailab-demo
smoke_test() {
  step "smoke test"
  [ "$DRY" = 1 ] && { echo "    (dry-run) log in through the Portal, run a task on the offline model"; return; }
  local tok base=$API_URL; tok=$(dev_token); JAR=$(mktemp)

  [ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer dev-demo-token' "$base/v1/runs")" = 401 ] && ok "the published default token is rejected" || die "the default dev token still works"
  if [ "$WITH_PORTAL" = 1 ]; then
    local p=http://127.0.0.1:$PORTAL_PORT
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$p/api/v1/runs")" = 401 ] && ok "Portal API proxy refuses anonymous requests" || die "Portal proxy is open to anonymous requests"
    [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "{\"username\":\"admin\",\"password\":\"$tok\"}" "$p/api/auth/login")" = 401 ] && ok "the API token is not accepted as a password" || die "the API token works as a Portal password"
    curl -fsS -c "$JAR" -X POST -H 'Content-Type: application/json' -d "{\"username\":\"admin\",\"password\":\"$(admin_password)\"}" "$p/api/auth/login" >/dev/null || die "login through the Portal failed"
    grep -q HttpOnly "$JAR" && ok "login works; session cookie is httpOnly" || die "session cookie is not httpOnly"
    curl -fsS -b "$JAR" "$p/api/v1/models" | grep -q '"models"' && ok "models are listed through the Portal proxy" || die "models not reachable through the Portal"
  fi

  [ "$(curl -s -o /dev/null -w '%{http_code}' "$base/internal/runs/not-public")" = 404 ] || die "internal API is exposed on the public gateway"
  local models; models=$(curl -fsS -H "Authorization: Bearer $tok" "$base/v1/models" | python3 -c 'import sys,json; print(" ".join(m["id"] for m in json.load(sys.stdin)["models"]))')
  ok "selectable models: $models"
  smoke_run() {  # model_id prompt seconds
    local rid st i
    rid=$(curl -fsS -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -d "{\"prompt\":\"$2\",\"model\":\"$1\"}" "$base/v1/runs" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])') || die "could not create a run on $1"
    for i in $(seq 1 "$3"); do
      st=$(curl -fsS -H "Authorization: Bearer $tok" "$base/v1/runs/$rid" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["status"], d["error"] or "")')
      case "$st" in succeeded*) ok "task on $1 succeeded (${i}s)"; return 0 ;; failed*|canceled*) die "task on $1 ended: $st" ;; esac
      sleep 1
    done
    die "task on $1 did not finish in $3 s (run $rid)"
  }
  case " $models " in *" ailab-demo "*) smoke_run ailab-demo "你好，在沙箱里执行一条命令" 120 ;; esac
  if [ "$SMOKE_REAL" = 1 ]; then
    case " $models " in
      *" $DEFAULT_MODEL "*) [ "$DEFAULT_MODEL" = ailab-demo ] || smoke_run "$DEFAULT_MODEL" "在沙箱里执行 echo deploy-ok，然后一句话告诉我输出" 240 ;;
      *) warn "$DEFAULT_MODEL unavailable (no key): skipping the real-model task" ;;
    esac
  fi
}
smoke_test
rm -f "$JAR"
