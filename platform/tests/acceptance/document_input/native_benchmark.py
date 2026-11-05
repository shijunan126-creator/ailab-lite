"""Measure the real native parser in one disposable, networkless Office container.

No user service, volume, credential, Docker context setting or image is changed.
The current runner is injected into the pinned runtime; each parse has a fresh
process/output directory. Repeat is an OS-cache repeat, not a derived-cache hit.
"""

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]


def benchmark(directory, *, context, image, repeats=5):
    directory = Path(directory).resolve()
    corpus = directory / "samples-a"
    manifest = json.loads((corpus / "manifest.json").read_text())
    name = "ailab-document-baseline-" + uuid4().hex[:12]
    docker = ["docker", "--context", context]
    results = []

    def run(args, *, timeout=90):
        reply = subprocess.run([*docker, *args], capture_output=True, timeout=timeout)
        if reply.returncode:
            raise RuntimeError(f"docker command failed ({reply.returncode}): {reply.stderr.decode('utf-8', 'replace')[-3000:]}")
        return reply.stdout

    image_id = run(["image", "inspect", image, "--format", "{{.Id}}"]).decode().strip()
    runner = ROOT / "vendor/office-suite/runtime/runner.mjs"
    helper = ROOT / "platform/ailab_platform/sandbox_tools/office_exec.py"
    try:
        run(
            [
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--cpus",
                "1",
                "--memory",
                "768m",
                "--pids-limit",
                "128",
                "--read-only",
                "--tmpfs",
                "/workspace:rw,size=256m,mode=1777",
                "--tmpfs",
                "/tmp:rw,size=64m,mode=1777",
                "--entrypoint",
                "sleep",
                image_id,
                "1200",
            ]
        )

        def put(local, target):
            script = (
                "import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); "
                "p.parent.mkdir(parents=True,exist_ok=True); f=p.open('wb'); "
                "shutil.copyfileobj(sys.stdin.buffer,f,65536); f.close()"
            )
            with Path(local).open("rb") as stream:
                reply = subprocess.run(
                    [*docker, "exec", "-i", name, "python", "-c", script, target], stdin=stream, capture_output=True, timeout=30
                )
            if reply.returncode:
                raise RuntimeError(reply.stderr.decode("utf-8", "replace")[-2000:])

        for item in manifest["files"]:
            put(corpus / item["name"], "/workspace/samples/" + item["name"])
        put(runner, "/workspace/office/runner.mjs")
        put(helper, "/workspace/office_exec.py")
        run(["exec", name, "ln", "-s", "/opt/ailab-office/node_modules", "/workspace/office/node_modules"])
        node = run(["exec", name, "node", "--version"]).decode().strip()
        for item in manifest["files"]:
            kind = Path(item["name"]).suffix.removeprefix(".")
            if kind not in {"pdf", "docx", "pptx", "xlsx"}:
                continue
            samples = []
            for iteration in range(repeats + 1):
                run(["exec", name, "python", "-c", "import shutil; shutil.rmtree('/workspace/out', ignore_errors=True)"])
                started = time.monotonic()
                run(
                    [
                        "exec",
                        name,
                        "python",
                        "/workspace/office_exec.py",
                        "60",
                        str(320 * 1024**2),
                        "node",
                        "--max-old-space-size=160",
                        "/workspace/office/runner.mjs",
                        "/workspace/samples/" + item["name"],
                        "/workspace/out",
                        kind,
                    ],
                    timeout=75,
                )
                wall = time.monotonic() - started
                parsed = json.loads(run(["exec", name, "cat", "/workspace/out/manifest.json"]))
                samples.append(
                    {
                        "iteration": iteration,
                        "wall_seconds": wall,
                        "units": parsed["total_units"],
                        "chars": parsed["chars"],
                        "measurement": parsed["measurement"],
                    }
                )
            warm = sorted(sample["wall_seconds"] for sample in samples[1:])
            row = {
                "name": item["name"],
                "cold_process_seconds": samples[0]["wall_seconds"],
                "repeat_process_p50_seconds": statistics.median(warm),
                "repeat_process_p95_seconds": warm[math.ceil(0.95 * len(warm)) - 1],
                "samples": samples,
            }
            results.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "samples"}), flush=True)
        output = {
            "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "image_id": image_id,
            "node": node,
            "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
            "corpus_manifest_sha256": hashlib.sha256((corpus / "manifest.json").read_bytes()).hexdigest(),
            "container": {"cpus": 1, "memory_mib": 768, "node_heap_mib": 160, "node_rss_limit_mib": 320},
            "definition": "fresh process/output for every sample; cold OS disk cache not guaranteed; repeats are not derivative-cache hits",
            "results": results,
        }
        (directory / "native-baseline.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
        return output
    finally:
        subprocess.run([*docker, "rm", "-f", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--context", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    benchmark(args.directory, context=args.context, image=args.image, repeats=args.repeats)
