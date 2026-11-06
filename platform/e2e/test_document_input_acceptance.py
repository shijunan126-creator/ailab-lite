"""Opt-in real document capture publication, history and resource stability.

Run with AILAB_DOCUMENT_STABILITY=1 and a disposable Docker-backed E2E stack.
Measurements and heap evidence belong outside the repository.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PLATFORM, SERVICE_TOKENS
from .test_office_native import FIXTURES, _frame
from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(os.environ.get("AILAB_DOCUMENT_STABILITY") != "1", reason="opt-in document stability")]


def maintenance(stack):
    """Drive production expiry code on this test-owned database, never a developer DB."""
    assert stack.env and "e2e" in stack.env["AILAB_DATA_DIR"]
    script = """
from datetime import UTC, datetime, timedelta
from ailab_platform.db import SessionLocal
from ailab_platform.api.reaper import purge_deleted_conversations, drain_object_deletions
from ailab_platform.api.derived_assets import collect_batch
from ailab_platform.storage import get_storage
with SessionLocal() as db:
    for _ in range(30):
        purged=purge_deleted_conversations(db, datetime.now(UTC)+timedelta(days=31))
        deleted=drain_object_deletions(db,get_storage())
        collected=collect_batch(db,get_storage(),transient_only=True)
        if not (purged or deleted or collected): break
"""
    subprocess.run([sys.executable, "-c", script], cwd=PLATFORM, env=stack.env, check=True, timeout=60, capture_output=True)


def inspect(stack):
    script = """
import json
from sqlalchemy import select, func
from ailab_platform.db import SessionLocal
from ailab_platform.models import ViewerCapture, DerivedBudget, DerivedAsset, DocumentOperation
with SessionLocal() as db:
    captures=[{'id':r.id,'run_id':r.run_id,'key':r.storage_key,'digest':r.digest} for r in db.scalars(select(ViewerCapture))]
    budget=db.get(DerivedBudget,1)
    active=db.scalar(select(func.count()).select_from(DocumentOperation).where(DocumentOperation.status.in_(['running','canceling'])))
    print(json.dumps({'captures':captures,'reserved':budget.reserved_bytes,'used':budget.used_bytes,'active':active}))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=PLATFORM, env=stack.env, check=True, timeout=30, capture_output=True)
    return json.loads(result.stdout)


def heap_snapshot(cdp, path):
    with path.open("w") as out:

        def handler(data):
            out.write(data["chunk"])

        cdp.on("HeapProfiler.addHeapSnapshotChunk", handler)
        try:
            cdp.send("HeapProfiler.takeHeapSnapshot", {"reportProgress": False})
        finally:
            cdp.remove_listener("HeapProfiler.addHeapSnapshotChunk", handler)


def browser_resources(cdp):
    cdp.send("HeapProfiler.collectGarbage")
    heap = {r["name"]: r["value"] for r in cdp.send("Performance.getMetrics")["metrics"]}
    resources = {**cdp.send("Memory.getDOMCounters"), "heap_bytes": heap["JSHeapUsedSize"]}
    for kind in ["Map", "Set"]:
        prototype = cdp.send("Runtime.evaluate", {"expression": kind + ".prototype"})["result"]["objectId"]
        objects = cdp.send("Runtime.queryObjects", {"prototypeObjectId": prototype})["objects"]["objectId"]
        try:
            code = (
                "function(){let n=0;for(const m of this)for(const v of m.values())"
                "if(v&&typeof v==='object'&&typeof v.resolve==='function'&&typeof v.reject==='function')n++;return n}"
                if kind == "Map"
                else "function(){let n=0;for(const s of this)for(const v of s)if(typeof v==='string'&&/^run_.*:dv:/.test(v))n++;return n}"
            )
            resources["pending_promises" if kind == "Map" else "active_capture_calls"] = cdp.send(
                "Runtime.callFunctionOn", {"objectId": objects, "functionDeclaration": code, "returnByValue": True}
            )["result"]["value"]
        finally:
            cdp.send("Runtime.releaseObject", {"objectId": objects})
            cdp.send("Runtime.releaseObject", {"objectId": prototype})
    resources["workers"] = sum(t["type"] in {"worker", "shared_worker"} for t in cdp.send("Target.getTargets")["targetInfos"])
    return resources


def test_canonical_capture_history_fork_and_fifty_cycles(logged_in, stack, tmp_path):
    assert os.environ.get("AILAB_E2E_SANDBOX_PROVIDER") == "docker", "real Docker acceptance required"
    page = logged_in
    corpus = FIXTURES / "cjk.pdf"
    first = page.request.post(
        stack.portal + "/api/v1/files",
        multipart={"upload": {"name": "stability.pdf", "mimeType": "application/pdf", "buffer": corpus.read_bytes()}},
    )
    assert first.ok, first.text()
    fid = first.json()["id"]
    end = time.monotonic() + 60
    while page.request.get(f"{stack.portal}/api/v1/files/{fid}").json()["status"] != "ready":
        assert time.monotonic() < end
        page.wait_for_timeout(200)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    cdp.send("HeapProfiler.enable")
    observations, resources = [], []
    baseline = None
    for cycle in range(-3, 50):
        page.goto(stack.portal + "/?files=" + fid)
        _start_conversation(page, "原生收件验收", "test-office-vision")
        _wait_for_turns(page, 1)
        cid = page.url.rsplit("/", 1)[-1]
        page.locator(".turn .attach-chip.open").first.click()
        _frame(page, "stability.pdf").locator(".textLayer span").first.wait_for(timeout=30000)
        started = time.monotonic()
        _send(page, f"原生控制 {fid} pdf")
        _wait_for_turns(page, 2)
        turn = page.request.get(f"{stack.portal}/api/v1/conversations/{cid}").json()["turns"][-1]
        assert "capture_id" in turn["summary"] and '"source": "browser"' in turn["summary"], turn
        state = inspect(stack)
        assert len(state["captures"]) == 1, state
        capture = state["captures"][0]
        url = f"{stack.api}/internal/runs/{turn['id']}/captures/{capture['id']}"
        headers = {"X-Service-Token": SERVICE_TOKENS["ops"]}
        pixels = page.request.get(url, headers=headers)
        assert pixels.ok and hashlib.sha256(pixels.body()).hexdigest() == capture["digest"]
        page.reload()
        expect(page.locator(".turn").last).to_contain_text("capture_id")
        assert page.request.get(url, headers=headers).body() == pixels.body()
        if cycle == 0:
            fork = page.request.post(f"{stack.portal}/api/v1/conversations/{cid}/fork", data={"run_id": turn["id"], "mode": "chat"})
            assert fork.ok, fork.text()
            branch = fork.json()["id"]
            branched = inspect(stack)["captures"]
            assert len(branched) == 2 and len({r["key"] for r in branched}) == 1
            assert page.request.delete(f"{stack.portal}/api/v1/conversations/{cid}").ok
            maintenance(stack)
            surviving = inspect(stack)["captures"]
            assert len(surviving) == 1
            other = surviving[0]
            assert (
                page.request.get(f"{stack.api}/internal/runs/{other['run_id']}/captures/{other['id']}", headers=headers).body()
                == pixels.body()
            )
            assert page.request.delete(f"{stack.portal}/api/v1/conversations/{branch}").ok
        else:
            assert page.request.delete(f"{stack.portal}/api/v1/conversations/{cid}").ok
        page.goto(stack.portal + "/files")
        maintenance(stack)
        state = inspect(stack)
        assert not state["captures"] and state["reserved"] == 0 and state["active"] == 0, state
        assert not (Path(stack.env["AILAB_STORAGE_ROOT"]) / capture["key"]).exists()
        page.wait_for_timeout(10000)
        measured = browser_resources(cdp)
        if cycle == -1:
            baseline = measured
            heap_snapshot(cdp, stack.log_dir / "capture-baseline.heapsnapshot")
        if cycle in {24, 49}:
            heap_snapshot(cdp, stack.log_dir / f"capture-{cycle}.heapsnapshot")
        if cycle >= 0:
            resources.append({"cycle": cycle, **measured})
            observations.append({"cycle": cycle, "seconds": time.monotonic() - started, "bytes": len(pixels.body())})
            assert measured["pending_promises"] == baseline["pending_promises"]
            assert measured["active_capture_calls"] == baseline["active_capture_calls"] == 0
            assert measured["workers"] == baseline["workers"]
        (stack.log_dir / "capture-lifecycle.json").write_text(
            json.dumps({"baseline": baseline, "resources": resources, "observations": observations}, indent=2)
        )
    delta = (resources[-1]["heap_bytes"] - baseline["heap_bytes"]) / 1024**2
    assert delta <= 16, delta
    xs = list(range(50))
    ys = [r["heap_bytes"] / 1024**2 for r in resources]
    mean = sum(ys) / 50
    slope = sum((x - 24.5) * (y - mean) for x, y in zip(xs, ys)) / sum((x - 24.5) ** 2 for x in xs)
    assert slope <= 0.1, slope
