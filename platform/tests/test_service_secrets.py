"""Reconcile credentials against fake kubectl, including upgrades and idempotent re-runs."""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path


def test_service_secret_reconciliation_preserves_tokens_and_separates_model_keys(tmp_path):
    state = tmp_path / "secrets.json"
    state.write_text(
        json.dumps({"platform-secrets": {"AILAB_ADMIN_PASSWORD": "admin", "AILAB_KEY_MODEL": "model-key", "AILAB_SERVICE_TOKEN": "legacy"}})
    )
    kubectl = tmp_path / "kubectl"
    kubectl.write_text("""#!/usr/bin/env python3
import base64,json,os,sys
from pathlib import Path
p=Path(os.environ["FAKE_SECRET_STATE"])
s=json.loads(p.read_text())
a=sys.argv[1:]
if a[:2]==["-n","platform"]: a=a[2:]
if a[:2]==["get","ns"]: sys.exit(0)
if a[:2]==["get","secret"]:
    name=a[2]
    if os.environ.get("FAKE_FAIL_SECRET")==name: sys.exit(1)
    if name not in s: sys.exit(0 if "--ignore-not-found" in a else 1)
    if "-o" not in a: sys.exit(0)
    fmt=a[a.index("-o")+1]
    if fmt=="json": print(json.dumps({"data":{k:base64.b64encode(v.encode()).decode() for k,v in s[name].items()}}))
    else:
        key=fmt.split(".data.")[1].split("}")[0]
        print(base64.b64encode(s[name].get(key,"").encode()).decode())
elif a[:3]==["create","secret","generic"]:
    data={v.split("=",2)[1]:base64.b64encode(v.split("=",2)[2].encode()).decode() for v in a if v.startswith("--from-literal=")}
    print(json.dumps({"metadata":{"name":a[3]},"data":data}))
elif a[:2]==["apply","-f"]:
    doc=json.load(sys.stdin)
    s[doc["metadata"]["name"]]={k:base64.b64decode(v).decode() for k,v in doc["data"].items()}
    p.write_text(json.dumps(s))
else: sys.exit(2)
""")
    kubectl.chmod(0o755)
    kubeconfig = tmp_path / "config"
    kubeconfig.touch()
    env = {**os.environ, "KUBECTL": str(kubectl), "KUBECONFIG": str(kubeconfig), "FAKE_SECRET_STATE": str(state)}
    env.pop("DRY_RUN", None)
    script = Path(__file__).resolve().parents[1] / "scripts/k8s_secrets.sh"
    first = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=True)
    original = json.loads(state.read_text())
    subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=True)
    assert json.loads(state.read_text()) == original
    registry = json.loads(original["platform-service-verifiers"]["AILAB_SERVICE_CREDENTIALS"])
    tokens = []
    for name in ("api", "agent", "broker", "worker", "ops"):
        data = original[f"platform-{name}-identity"]
        assert set(data) == {"AILAB_SERVICE_CREDENTIAL"}
        token = data["AILAB_SERVICE_CREDENTIAL"]
        tokens.append(token)
        assert registry[name] == "sha256:" + hashlib.sha256(token.encode()).hexdigest()
        assert token not in first.stdout + first.stderr
    assert len(set(tokens)) == 5
    assert original["platform-model-secrets"] == {"AILAB_KEY_MODEL": "model-key"}
    # Private material is intentionally present only in the fake state, never the test output.
    assert base64.b64encode(b"model-key").decode() not in first.stdout

    failed = subprocess.run(
        ["bash", str(script)], env={**env, "FAKE_FAIL_SECRET": "platform-agent-identity"}, text=True, capture_output=True
    )
    assert failed.returncode != 0
    assert json.loads(state.read_text()) == original
