"""Exercise deployment decisions with fake commands; never touch Docker or a cluster."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "deploy.sh").read_text()


def _functions(*names):
    return "\n".join(re.search(rf"^{name}\(\) \{{.*?^\}}", SOURCE, re.M | re.S)[0] for name in names)


def test_image_import_compares_configuration_ids_not_manifest_digests(tmp_path):
    sandbox = tmp_path / "images" / "sbx-office"
    sandbox.mkdir(parents=True)
    (sandbox / "Dockerfile").write_text("FROM test")
    script = (
        _functions("build_images")
        + r"""
step() { :; }
ok() { :; }
warn() { :; }
die() { echo "$*" >&2; exit 1; }
run() { "$@"; }
nice() { shift 2; "$@"; }
docker() {
  case "$*" in
    *ailab.content-hash*) echo "$want" ;;
    *Id*) echo sha256:configuration ;;
    save*) [[ "$*" == *office:lite* ]] || exit 1; echo image-archive ;;
  esac
}
k3s() {
  case "$*" in
    *'images import'*) cat >/dev/null ;;
    *'images ls'*) echo 'ailab-platform:lite application/vnd.oci.image.manifest.v1+json sha256:manifest' ;;
    'crictl inspecti'*) echo '{"status":{"id":"sha256:configuration"}}' ;;
  esac
}
ROOT=$1 PLATFORM=$1/platform SANDBOX_IMAGE=office:lite PLATFORM_IMAGE=ailab-platform:lite SKIP_BUILD=0 DRY=0
build_images
"""
    )
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script, "test", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_failed_manifest_application_stops_deployment():
    script = (
        _functions("deploy_platform")
        + r"""
step() { :; }
ok() { :; }
die() { echo "$*" >&2; exit 1; }
curl() { return 0; }
k() { return 1; }
wait_http() { return 0; }
run() { [ "${1:-}" != kubectl ]; }
DRY=1 PLATFORM=/unused NS=platform OSB_URL=http://unused CATALOG=models.toml KEY_ENV='' MODEL_KEY='' INVITE_CODE=''
REGISTRATION='' KUBECTL=kubectl API_URL=http://unused BROKER_URL=http://unused
deploy_platform
"""
    )
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True)
    assert result.returncode != 0, "a failed kubectl apply must never be reported as a successful deployment"


def test_kubernetes_deploy_refuses_existing_docker_osb_config(tmp_path):
    import os

    home = tmp_path / "osb"
    home.mkdir()
    config = home / "sandbox.toml"
    config.write_text('[runtime]\ntype = "docker"\n[server]\nport = 8090\napi_key = "test"\n')
    binary = tmp_path / "venv/bin/opensandbox-server"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "platform/scripts/osb_up.sh")],
        env=os.environ
        | {
            "OSB_HOME": str(home),
            "OSB_VENV": str(binary.parents[1]),
            "OSB_CONFIG": str(config),
            "OSB_RUNTIME": "kubernetes",
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "existing config runtime differs" in result.stderr


def test_osb_healthy_endpoint_cannot_reuse_untracked_or_changed_process(tmp_path):
    import json

    helper = ROOT / "platform/scripts/osb_process.py"
    config = tmp_path / "config.toml"
    config.write_text('[runtime]\ntype = "kubernetes"\n')
    for record in (None, {"pid": 0, "config": str(config), "sha256": "stale"}):
        if record:
            (tmp_path / "server-process.json").write_text(json.dumps(record))
        result = subprocess.run(["python3", str(helper), "check", str(tmp_path), str(config)], capture_output=True, text=True)
        assert result.returncode != 0
        assert "not bound to this reviewed startup config" in result.stderr


def test_docker_osb_startup_rejects_dns_only_and_generates_packet_filtering(tmp_path):
    import os
    import tomllib

    home, venv, commands = (tmp_path / name for name in ("osb", "venv", "commands"))
    home.mkdir()
    (venv / "bin").mkdir(parents=True)
    commands.mkdir()
    config = home / "sandbox.toml"
    example = '[server]\nhost = "127.0.0.1"\nport = 8080\n[runtime]\ntype = "docker"\n[egress]\nmode = "dns"\n'
    binary = venv / "bin/opensandbox-server"
    binary.write_text("#!/bin/sh\ncat > \"$2\" <<'TOML'\n" + example + "TOML\n")
    binary.chmod(0o755)
    curl = commands / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    env = os.environ | {
        "OSB_HOME": str(home),
        "OSB_VENV": str(venv),
        "OSB_CONFIG": str(config),
        "OSB_RUNTIME": "docker",
        "OSB_PORT": "19091",
        "OSB_API_KEY": 'disposable-quote"-key',
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
    }
    config.write_text(example)
    result = subprocess.run(["bash", str(ROOT / "platform/scripts/osb_up.sh")], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "Docker deny-all requires" in result.stderr
    assert config.read_text() == example  # no silent rewrite of an existing deployment
    config.unlink()
    result = subprocess.run(["bash", str(ROOT / "platform/scripts/osb_up.sh")], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "not bound to this reviewed startup config" in result.stderr
    generated = tomllib.loads(config.read_text())
    assert generated["egress"]["mode"] == "dns+nft"
    assert generated["server"]["port"] == 19091
    assert generated["server"]["api_key"] == env["OSB_API_KEY"]


def test_osb_process_record_freezes_referenced_template(tmp_path, monkeypatch):
    import runpy
    import sys

    helper = ROOT / "platform/scripts/osb_process.py"
    config, template = tmp_path / "config.toml", tmp_path / "template.yaml"
    template.write_text("reviewed template")
    config.write_text(f'[kubernetes]\nbatchsandbox_template_file = "{template}"\n')
    monkeypatch.setattr(sys, "argv", [str(helper), "record", str(tmp_path), str(config), "12345"])
    runpy.run_path(str(helper), run_name="__main__")
    original = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: f"python\0--config\0{config}\0".encode() if str(path) == "/proc/12345/cmdline" else original(path)
    )
    monkeypatch.setattr(sys, "argv", [str(helper), "check", str(tmp_path), str(config)])
    runpy.run_path(str(helper), run_name="__main__")
    template.write_text("changed after startup")
    import pytest

    with pytest.raises(SystemExit, match="not bound to this reviewed startup config"):
        runpy.run_path(str(helper), run_name="__main__")
