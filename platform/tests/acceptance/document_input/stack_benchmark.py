"""Real upload baseline on owned API/Broker/Worker/Redis/ClamAV/Docker resources.

Uses no existing database, volume or Broker ownership domain. Never loads model
keys or calls models. Logs/measurements stay outside the repository. Inspect the
retained state directory after failures, then remove it; containers/processes are
always terminated in finally. This is not browser or full-answer acceptance.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import secrets
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[4]


def benchmark(directory, *, context, output):
    directory = Path(directory).resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError("keep measurements outside the repository")
    if Path(output).name != output:
        raise ValueError("output must be a filename")
    destination = directory / output
    if destination.exists():
        raise FileExistsError("refusing to replace a frozen measurement")
    name = "ailab-doc-baseline-" + uuid.uuid4().hex[:10]
    docker = ["docker", "--context", context]
    processes = []
    files = []
    ports = [31380, 31381, 31383, 31379, 31310]
    for port in ports:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
    state = Path(tempfile.mkdtemp(prefix="ailab-doc-stack-"))
    print("STATE", state, flush=True)
    (state / "tmp").mkdir()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("AILAB_")},
        "AILAB_ENV_FILE": str(state / "none.env"),
        "AILAB_ENV": "test",
        "AILAB_PROFILE": "test",
        "AILAB_DATA_DIR": str(state),
        "AILAB_LOG_DIR": str(state / "logs"),
        "AILAB_STORAGE_ROOT": str(state / "objects"),
        "AILAB_DATABASE_URL": "sqlite:///" + str(state / "platform.db"),
        "AILAB_QUEUE_BACKEND": "redis",
        "AILAB_REDIS_URL": "redis://127.0.0.1:31379/0",
        "AILAB_API_BASE_URL": "http://127.0.0.1:31380",
        "AILAB_BROKER_BASE_URL": "http://127.0.0.1:31381",
        "AILAB_WORKER_BASE_URL": "http://127.0.0.1:31383",
        "AILAB_SCANNER_BACKEND": "clamav",
        "AILAB_CLAMD_ADDRESS": "tcp://127.0.0.1:31310",
        "AILAB_SANDBOX_PROVIDER": "docker",
        "AILAB_SANDBOX_IMAGE": "sha256:5bd2e9250ddbc139916067007b477399204e6e4d79a955746d17923bbf1cc5b3",
        "AILAB_BROKER_OWNER": name,
        "AILAB_DEV_TOKEN": secrets.token_urlsafe(32),
        "AILAB_SERVICE_TOKEN": secrets.token_urlsafe(32),
        "DOCKER_CONTEXT": context,
        "TMPDIR": str(state / "tmp"),
    }
    # Do not inherit private application credentials or developer database configuration.
    for key in list(env):
        if key.startswith("AILAB_KEY_"):
            del env[key]
    credentials = {role: secrets.token_urlsafe(32) for role in ["api", "agent", "broker", "worker", "ops"]}
    env.update({"AILAB_SERVICE_" + role.upper() + "_TOKEN": token for role, token in credentials.items()})
    env["AILAB_SERVICE_CREDENTIALS"] = json.dumps(
        {role: "sha256:" + hashlib.sha256(token.encode()).hexdigest() for role, token in credentials.items()}
    )
    report = {
        "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "real API/Worker/Broker/ClamAV/Docker upload chain; no Agent/model/browser",
        "results": [],
    }

    def launch(label, args):
        out = (state / (label + ".log")).open("wb")
        files.append(out)
        proc = subprocess.Popen(args, cwd=ROOT / "platform", env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append((label, proc))
        return proc

    try:
        subprocess.run(
            [
                *docker,
                "run",
                "--rm",
                "-d",
                "--name",
                name + "-clam",
                "--network",
                "bridge",
                "--cpus",
                "0.5",
                "--memory",
                "1024m",
                "-p",
                "127.0.0.1:31310:3310",
                "-e",
                "CLAMAV_NO_FRESHCLAMD=true",
                "sha256:d7ede3cefeaf3b8a72ebcdee849d13f504bbca3f3b3cfade5bc7b6ee06a4f62c",
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        launch("redis", ["redis-server", "--bind", "127.0.0.1", "--port", "31379", "--dir", str(state), "--save", "", "--appendonly", "no"])
        python = str(ROOT / "platform/.venv/bin/python")
        for service, port in [("api", 31380), ("broker", 31381), ("worker", 31383)]:
            launch(
                service,
                [
                    python,
                    "-m",
                    "uvicorn",
                    f"ailab_platform.{service}.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
            )
        with httpx.Client(
            base_url="http://127.0.0.1:31380", headers={"Authorization": "Bearer " + env["AILAB_DEV_TOKEN"]}, timeout=30
        ) as client:
            deadline = time.monotonic() + 150
            for port in [31380, 31381, 31383]:
                while True:
                    for label, p in processes:
                        if p.poll() is not None:
                            raise RuntimeError(label + " exited; inspect owned logs")
                    try:
                        if client.get(f"http://127.0.0.1:{port}/healthz").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("service startup")
                    time.sleep(0.25)
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", 31310), timeout=1) as connection:
                        connection.sendall(b"zPING\0")
                        if b"PONG" in connection.recv(100):
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("isolated ClamAV startup")
                time.sleep(0.5)
            print("READY real scanner and three services", flush=True)
            for filename in ["native-small.pdf", "native-long-500.pdf", "scan-40.pdf", "mixed-12.docx", "mixed-12.pptx", "mixed.xlsx"]:
                for iteration in range(2):
                    source = directory / "samples-a" / filename
                    started = time.monotonic()
                    with source.open("rb") as stream:
                        response = client.post(
                            "/v1/files?disposition=copy",
                            files={
                                "upload": (
                                    f"{iteration}-{filename}",
                                    stream,
                                    mimetypes.guess_type(filename)[0] or "application/octet-stream",
                                )
                            },
                        )
                    response.raise_for_status()
                    fid = response.json()["id"]
                    peak = {}
                    deadline = time.monotonic() + 240
                    while True:
                        response = client.get("/v1/files/" + fid)
                        response.raise_for_status()
                        file = response.json()
                        for label, proc in processes:
                            if label != "redis":
                                try:
                                    peak[label] = max(
                                        peak.get(label, 0),
                                        int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(proc.pid)], timeout=1).strip()) * 1024,
                                    )
                                except (ValueError, subprocess.SubprocessError):
                                    pass
                        if file["status"] != "pending":
                            break
                        if time.monotonic() > deadline:
                            raise TimeoutError("file readiness " + filename)
                        time.sleep(0.1)
                    meta = file.get("meta") or {}
                    objects_directory = state / "objects" / "derived" / fid
                    objects = [p for p in objects_directory.rglob("*") if p.is_file()] if objects_directory.exists() else []
                    row = {
                        "file": filename,
                        "iteration": iteration,
                        "seconds": time.monotonic() - started,
                        "status": file["status"],
                        "scan": meta.get("scan"),
                        "extract": meta.get("extract"),
                        "preview_status": meta.get("preview", {}).get("status"),
                        "cache": meta.get("cache"),
                        "derived_objects_at_ready": len(objects),
                        "derived_bytes_at_ready": sum(p.stat().st_size for p in objects),
                        "peak_service_rss": peak,
                    }
                    report["results"].append(row)
                    print(json.dumps(row), flush=True)
            destination.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        for label, proc in reversed(processes):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for label, proc in reversed(processes):
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        subprocess.run([*docker, "rm", "-f", name + "-clam"], capture_output=True, timeout=30)
        refs = subprocess.run(
            [*docker, "ps", "-aq", "--filter", "label=ailab.owner=" + name], capture_output=True, text=True, timeout=20
        ).stdout.split()
        for ref in refs:
            subprocess.run([*docker, "rm", "-f", ref], capture_output=True, timeout=30)
        for f in files:
            f.close()
        print("STOPPED owned processes/containers; state retained for inspection", state, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", default="upload-baseline.json")
    args = parser.parse_args()
    benchmark(args.directory, context=args.context, output=args.output)
