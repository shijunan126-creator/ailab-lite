#!/usr/bin/env bash
# Start the OpenSandbox dev server used by AILAB_SANDBOX_PROVIDER=opensandbox.
set -euo pipefail
umask 077

OSB_HOME=${OSB_HOME:-$HOME/osb}
OSB_VENV=${OSB_VENV:-$HOME/osb-venv}
OSB_CONFIG=${OSB_CONFIG:-$OSB_HOME/sandbox.toml}
OSB_PORT=${OSB_PORT:-8090}
OSB_RUNTIME=${OSB_RUNTIME:-docker}
OSB_API_KEY=${OSB_API_KEY:-${AILAB_OSB_API_KEY:-dev-osb-key}}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

if [ ! -x "$OSB_VENV/bin/opensandbox-server" ]; then
  echo "[osb_up] installing opensandbox-server into $OSB_VENV"
  python3 -m venv "$OSB_VENV"
  "$OSB_VENV/bin/pip" install -q --upgrade pip
  "$OSB_VENV/bin/pip" install -q 'opensandbox-server==1.1.1'
fi

mkdir -p "$(dirname "$OSB_CONFIG")" "$OSB_HOME"
if [ ! -f "$OSB_CONFIG" ]; then
  if [ "$OSB_RUNTIME" = kubernetes ]; then
    cp "$ROOT/k3s/opensandbox-template.yaml" "$OSB_HOME/opensandbox-template.yaml"
    cp "$ROOT/k3s/opensandbox.example.toml" "$OSB_CONFIG"
    OSB_HOME="$OSB_HOME" OSB_CONFIG="$OSB_CONFIG" OSB_API_KEY="$OSB_API_KEY" python3 - <<'CONFIG'
import json, os
from pathlib import Path
p = Path(os.environ["OSB_CONFIG"])
s = p.read_text().replace('"REPLACE-WITH-PRIVATE-KEY"', json.dumps(os.environ["OSB_API_KEY"]))
s = s.replace('"/etc/ailab/opensandbox-template.yaml"', json.dumps(str(Path(os.environ["OSB_HOME"]) / "opensandbox-template.yaml")))
p.write_text(s)
p.chmod(0o600)
CONFIG
  elif [ "$OSB_RUNTIME" = docker ]; then
    "$OSB_VENV/bin/opensandbox-server" init-config "$OSB_CONFIG" --example docker
  else
    echo '[osb_up] unknown OSB_RUNTIME' >&2; exit 1
  fi
fi
OSB_CONFIG="$OSB_CONFIG" OSB_RUNTIME="$OSB_RUNTIME" OSB_ROOT="$ROOT" python3 - <<'CHECK'
import os, tomllib
from pathlib import Path
with open(os.environ["OSB_CONFIG"], "rb") as f:
    config = tomllib.load(f)
if config.get("runtime", {}).get("type") != os.environ["OSB_RUNTIME"]:
    raise SystemExit("[osb_up] existing config runtime differs; refusing to reuse an unsafe backend")
if os.environ["OSB_RUNTIME"] == "kubernetes":
    k = config.get("kubernetes", {})
    secure = config.get("secure_runtime", {})
    if k.get("namespace") != "sandbox" or secure.get("type") != "gvisor" or secure.get("k8s_runtime_class") != "gvisor":
        raise SystemExit("[osb_up] Kubernetes mode requires sandbox namespace and gvisor runtime")
    template = Path(k.get("batchsandbox_template_file", "")).expanduser()
    expected = Path(os.environ["OSB_ROOT"]) / "k3s/opensandbox-template.yaml"
    if not template.is_file() or template.read_bytes() != expected.read_bytes():
        raise SystemExit("[osb_up] install the reviewed node-owned OpenSandbox template before starting")
CHECK
sed -i "s/^port = 8080/port = $OSB_PORT/" "$OSB_CONFIG"
if ! grep -q '^api_key = ' "$OSB_CONFIG"; then
  sed -i "s|^# api_key = .*|api_key = \"$OSB_API_KEY\"|" "$OSB_CONFIG"
fi

if curl -fsS --max-time 3 "http://127.0.0.1:$OSB_PORT/health" >/dev/null 2>&1; then
  if [ "$OSB_RUNTIME" = kubernetes ]; then
    python3 "$ROOT/platform/scripts/osb_process.py" check "$OSB_HOME" "$OSB_CONFIG"
  fi
  echo "[osb_up] already healthy on :$OSB_PORT"
  exit 0
fi

cd "$OSB_HOME"
setsid nohup "$OSB_VENV/bin/opensandbox-server" --config "$OSB_CONFIG" > "$OSB_HOME/server.log" 2>&1 < /dev/null &
server_pid=$!
if [ "$OSB_RUNTIME" = kubernetes ]; then
  python3 "$ROOT/platform/scripts/osb_process.py" record "$OSB_HOME" "$OSB_CONFIG" "$server_pid"
fi
for _ in $(seq 1 40); do
  if curl -fsS --max-time 3 "http://127.0.0.1:$OSB_PORT/health" >/dev/null 2>&1; then
    echo "[osb_up] ready on http://127.0.0.1:$OSB_PORT"
    exit 0
  fi
  sleep 0.5
done
echo "[osb_up] failed to start; last log lines:"
tail -30 "$OSB_HOME/server.log"
exit 1
