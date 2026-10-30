#!/usr/bin/env python3
"""Compare archived main and a frozen dirty tree using the real E2E stack.

Run with platform/.venv/bin/python platform/scripts/bench_portal.py.
Only /tmp/ailab-perf is used; all owned services and temporary output are removed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path("/tmp/ailab-perf")
BASE = "97793e601d90d0e9db11d60621c4f1352450166b"


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def assets(build):
    manifest = next(build.glob(".next/server/app/**/c/[[]id[]]/page_client-reference-manifest.js"))
    script = """
const fs=require('fs'), vm=require('vm'); const context={}; vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'),context);
const manifest=Object.values(context.__RSC_MANIFEST)[0];
console.log(JSON.stringify([...new Set(Object.values(manifest.entryJSFiles).flat())]));
"""
    entries = json.loads(command(["node", "-e", script, str(manifest)], capture_output=True, text=True).stdout)

    def normalize(name):
        return name.removeprefix("/_next/").removeprefix("_next/")

    entries = {normalize(name) for name in entries}
    chunks = []
    for chunk in (build / ".next/static/chunks").glob("*.js"):
        # Concrete library implementation marker, not arbitrary wrapper/import filenames.
        if "createLibrary" in chunk.read_text():
            name = str(chunk.relative_to(build / ".next"))
            chunks.append({"name": name, "gzip": len(gzip.compress(chunk.read_bytes(), mtime=0)), "entry": name in entries})
    return {
        "entry_gzip": sum(len(gzip.compress((build / ".next" / name).read_bytes(), mtime=0)) for name in entries),
        "entry_files": len(entries),
        "genui_chunks": chunks,
    }


def parser_measure(copy):
    portal = copy / "platform/portal"
    if not (portal / "components/genui/catalog/schema.ts").exists():
        return None
    script = portal / "perf-parse.mts"
    script.write_text("""import {createParser} from '@openuidev/react-lang';
import {schemaLibrary} from './components/genui/catalog/schema.ts';
const parser=createParser(schemaLibrary.toJSONSchema(), 'Stack');
const source='root = Text("'+'汉'.repeat(15985)+'")';
const start=performance.now(); const result=parser.parse(source); const ms=performance.now()-start;
if (!result?.root) throw Error('parse failed');
console.log(JSON.stringify({chars:source.length, ms}));
""")
    result = command(
        ["node", "--experimental-strip-types", str(script)],
        cwd=portal,
        capture_output=True,
        text=True,
        env={**os.environ, "OPENUI_TELEMETRY_DISABLED": "1"},
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def summarize(rows):
    return {
        cpu: {
            key: (
                max
                if key in {"input_max", "long_tasks", "mutations", "mount_mutations", "settle_mutations", "visible_max"}
                else statistics.median
            )(row[key] for row in rows if row["cpu"] == cpu)
            for key in (
                "dom",
                "input_p50",
                "input_p95",
                "input_max",
                "long_tasks",
                "frame_p95",
                "mutations",
                "mount_mutations",
                "settle_mutations",
                "chars_per_s",
                "visible_p95",
                "visible_max",
            )
        }
        for cpu in (1, 4)
    }


def table(results):
    print("\n| 指标 | CPU | baseline | current | 预算 | 结果 |")
    print("|---|---:|---:|---:|---|---|")
    for cpu in (1, 4):
        base, cur = (results[name]["summary"][cpu] for name in ("base", "cur"))
        for key, label, limit in [
            ("input_p95", "输入 p50/p95/max ms", base["input_p95"] * 1.1),
            ("dom", "DOM", base["dom"] * 1.2),
            ("long_tasks", "长任务", 0),
            ("frame_p95", "帧 p95 ms", 16.7 if cpu == 1 else 33),
            ("mutations", "历史轮次内容 mutations：流式期间（代理）", 0),
            ("visible_max", "delta→DOM/presentation p95/max ms", 150),
        ]:
            status = "PASS" if round(cur[key], 3) <= round(limit, 3) else "FAIL"
            fields = (
                ("input_p50", "input_p95", "input_max")
                if key == "input_p95"
                else (("visible_p95", "visible_max") if key == "visible_max" else (key,))
            )
            values = [" / ".join(f"{row[field]:.2f}" for field in fields) for row in (base, cur)]
            print(f"| {label} | {cpu}× | {values[0]} | {values[1]} | ≤{limit:.2f} | {status} |")
        for key, label in [
            ("mount_mutations", "离屏轮次挂载/卸载 mutations（信息）"),
            ("settle_mutations", "完成后 250ms 内 mutations（信息）"),
        ]:
            print(f"| {label} | {cpu}× | {base[key]:.2f} | {cur[key]:.2f} | — | — |")
    b, c = (results[name]["assets"]["entry_gzip"] for name in ("base", "cur"))
    print(f"| entry JS gzip B | — | {b} | {c} | ≤{b + 80000} | {'PASS' if c <= b + 80000 else 'FAIL'} |")
    chunks = results["cur"]["assets"]["genui_chunks"]
    size = sum(chunk["gzip"] for chunk in chunks)
    print(f"| GenUI gzip B | — | — | {size} | ≤60000 | {'PASS' if chunks and size <= 60000 else 'FAIL'} |")
    parsed = results["cur"]["parse"]
    if parsed:
        print(f"| GenUI {parsed['chars']} 字符单次解析 ms | — | — | {parsed['ms']:.3f} | ≤8 | {'PASS' if parsed['ms'] <= 8 else 'FAIL'} |")
    print("GenUI in entry:", any(chunk["entry"] for chunk in chunks))
    for name in ("base", "cur"):
        rates = [row["chars_per_s"] for row in results[name]["rows"]]
        print(f"{name}: actual SSE rate {min(rates):.2f}–{max(rates):.2f} chars/s")


def main():
    if WORK.exists():
        raise SystemExit(f"{WORK} already exists; inspect it before removing (never reuse a stale benchmark stack)")
    for port in (16450, 18450, 18451, 18452, 18453, 13450):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))  # refuse collisions, never terminate other services
    WORK.mkdir()
    redis = None
    try:
        base = WORK / "base"
        base.mkdir()
        archive = WORK / "base.tar"
        command(["git", "archive", "--format=tar", "--output", str(archive), BASE], cwd=ROOT)
        with tarfile.open(archive) as handle:
            handle.extractall(base, filter="data")
        cur = WORK / "cur"
        command(
            [
                "rsync",
                "-a",
                "--exclude=.git",
                "--exclude=node_modules",
                "--exclude=.next",
                "--exclude=.venv",
                "--exclude=__pycache__",
                "--exclude=.pytest_cache",
                "--exclude=e2e-logs",
                str(ROOT) + "/",
                str(cur) + "/",
            ]
        )
        # Freeze once. Both the UI and backend engineers may continue editing the source worktree.
        digest = hashlib.sha256()
        for path in sorted((cur / "platform/portal").rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(cur)).encode())
                digest.update(path.read_bytes())
        print(f"CURRENT_PORTAL_SHA256={digest.hexdigest()}", flush=True)
        installed = ROOT / "platform/portal/node_modules"
        locks = [json.loads((copy / "platform/portal/package-lock.json").read_text())["packages"] for copy in (base, cur)]
        for package in ("next", "react", "react-dom", "typescript"):
            versions = [lock["node_modules/" + package]["version"] for lock in locks]
            versions.append(json.loads((installed / package / "package.json").read_text())["version"])
            if len(set(versions)) != 1:
                raise RuntimeError(f"dependency drift for {package}: {versions}; install matching copies before comparing")
        for copy in (base, cur):
            perf = copy / "platform/e2e/perf"
            if copy == base:
                shutil.copytree(cur / "platform/e2e/perf", perf)
            # Exact same installed Next/React toolchain; no dependency writes to the source worktree.
            command(["cp", "-R", str(ROOT / "platform/portal/node_modules"), str(copy / "platform/portal/node_modules")])
        redis_log = (WORK / "redis.log").open("w")
        redis = subprocess.Popen(
            ["redis-server", "--bind", "127.0.0.1", "--port", "16450", "--save", "", "--appendonly", "no"],
            stdout=redis_log,
            stderr=subprocess.STDOUT,
        )
        results = {}
        for name in ("base", "cur"):
            copy = WORK / name
            temp = WORK / (name + "-pytest")
            output = WORK / (name + ".jsonl")
            env = {k: v for k, v in os.environ.items() if not k.startswith("AILAB_")}
            env.update(
                AILAB_E2E="1",
                AILAB_PERF="1",
                AILAB_TEST_REDIS_URL="redis://127.0.0.1:16450/13",
                AILAB_E2E_BASE_PORT="18450",
                AILAB_E2E_PORTAL_PORT="13450",
                AILAB_E2E_LOG_DIR=str(WORK / (name + "-logs")),
                AILAB_PERF_OUTPUT=str(output),
                PYTHONPATH=str(copy / "platform"),
                OPENUI_TELEMETRY_DISABLED="1",
            )
            print(f"RUNNING {name}", flush=True)
            command([sys.executable, "-m", "pytest", "e2e/perf", "-x", "-s", "--basetemp", str(temp)], cwd=copy / "platform", env=env)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            builds = list(temp.glob("e2e*/portal"))
            results[name] = {"summary": summarize(rows), "assets": assets(builds[0]), "parse": parser_measure(copy), "rows": rows}
        print(f"ENV={platform.platform()}, Python {platform.python_version()}")
        table(results)
    finally:
        if redis:
            redis.terminate()
            redis.wait(timeout=10)
            redis_log.close()
        shutil.rmtree(WORK)


if __name__ == "__main__":
    main()
