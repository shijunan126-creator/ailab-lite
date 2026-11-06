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


def wait_ready(page, stack, fid):
    end = time.monotonic() + 60
    while page.request.get(f"{stack.portal}/api/v1/files/{fid}").json()["status"] != "ready":
        assert time.monotonic() < end, "safety scan did not finish"
        page.wait_for_timeout(200)


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
        page.get_by_role("radio", name="对话", exact=True).click()
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
        headers = {"X-Service-Token": SERVICE_TOKENS["agent"]}
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
    # Keep this run exclusive: unrelated library uploads change these counts.
    # A fixed-library run must return to the same mounted document/listeners.
    assert resources[-1]["documents"] == baseline["documents"]
    assert resources[-1]["jsEventListeners"] == baseline["jsEventListeners"]
    assert resources[-1]["nodes"] == baseline["nodes"]
    xs = list(range(50))
    ys = [r["heap_bytes"] / 1024**2 for r in resources]
    mean = sum(ys) / 50
    slope = sum((x - 24.5) * (y - mean) for x, y in zip(xs, ys)) / sum((x - 24.5) ** 2 for x in xs)
    assert slope <= 0.1, slope


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_late_browser_pixels_cannot_publish_after_fallback_or_cancellation(logged_in, stack, failure):
    page = logged_in
    upload = page.request.post(
        stack.portal + "/api/v1/files",
        multipart={
            "upload": {
                "name": f"late-{failure}.docx",
                "mimeType": "application/octet-stream",
                "buffer": (FIXTURES / "report.docx").read_bytes(),
            }
        },
    )
    assert upload.ok
    fid = upload.json()["id"]
    wait_ready(page, stack, fid)
    page.goto(stack.portal + "/?files=" + fid)
    page.get_by_role("radio", name="对话", exact=True).click()
    cid = _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    page.locator(".turn .attach-chip.open").first.click()
    _frame(page, f"late-{failure}.docx").locator("section.office-docx > article p").first.wait_for()
    held = []
    page.route("**/client-calls/*/result", lambda route: held.append(route))
    _send(page, f"原生控制 {fid} docx")
    end = time.monotonic() + 30
    while not held:
        assert time.monotonic() < end, "no real browser capture reached the publication boundary"
        page.wait_for_timeout(100)
    request = held[0].request
    payload = request.post_data_json
    turn = page.request.get(f"{stack.portal}/api/v1/conversations/{cid}").json()["turns"][-1]
    if failure == "cancel":
        canceled = page.request.post(f"{stack.portal}/api/v1/runs/{turn['id']}/cancel")
        assert canceled.ok, canceled.text()
    else:
        _wait_for_turns(page, 2, seconds=120)
        done = page.request.get(f"{stack.portal}/api/v1/conversations/{cid}").json()["turns"][-1]
        assert '"source": "office-pdf"' in done["summary"], done
        assert '"input": "image"' in done["summary"]
    late = page.request.post(request.url, data=payload)
    assert late.status == 409, late.text()
    assert not inspect(stack)["captures"], "late client result must not create a canonical asset"
    held[0].abort()
    assert page.request.delete(f"{stack.portal}/api/v1/conversations/{cid}").ok
    maintenance(stack)
    assert inspect(stack)["reserved"] == 0


@pytest.mark.parametrize("failure", ["storage", "version", "deleted"])
def test_capture_publication_rechecks_storage_version_and_deleted_source(logged_in, stack, failure):
    page = logged_in
    uploaded = page.request.post(
        stack.portal + "/api/v1/files?disposition=copy",
        multipart={
            "upload": {"name": f"publication-{failure}.pdf", "mimeType": "application/pdf", "buffer": (FIXTURES / "cjk.pdf").read_bytes()}
        },
    )
    assert uploaded.ok
    fid = uploaded.json()["id"]
    wait_ready(page, stack, fid)
    page.goto(stack.portal + "/?files=" + fid)
    page.get_by_role("radio", name="对话", exact=True).click()
    cid = _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    page.locator(".turn .attach-chip.open").first.click()
    _frame(page, f"publication-{failure}.pdf").locator(".textLayer span").first.wait_for()
    held = []
    page.route("**/client-calls/*/result", lambda route: held.append(route))
    _send(page, f"原生控制 {fid} pdf")
    end = time.monotonic() + 30
    while not held:
        assert time.monotonic() < end
        page.wait_for_timeout(50)
    request = held[0].request
    original = request.post_data_json
    before = {r["id"] for r in inspect(stack)["captures"]}
    if failure == "storage":
        # A real filesystem denial at atomic rename, confined to this upload's
        # private capture directory. Never change the shared store's permissions.
        target = Path(stack.env["AILAB_STORAGE_ROOT"]) / "derived" / fid / "captures"
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o500)
        try:
            denied = page.request.post(request.url, data=original)
            assert denied.status == 500, denied.text()
            assert {r["id"] for r in inspect(stack)["captures"]} == before
            assert not list(target.iterdir())
        finally:
            target.chmod(0o700)
    elif failure == "version":
        stale = {**original, "value": {**original["value"], "version": "0" * 64}}
        rejected = page.request.post(request.url, data=stale)
        assert rejected.status == 409 and rejected.json()["detail"] == "capture version changed"
        assert {r["id"] for r in inspect(stack)["captures"]} == before
    else:
        assert page.request.delete(f"{stack.portal}/api/v1/files/{fid}").ok
        rejected = page.request.post(request.url, data=original)
        assert rejected.status in {404, 409}, rejected.text()
        assert {r["id"] for r in inspect(stack)["captures"]} == before
    if failure != "deleted":
        accepted = page.request.post(request.url, data=original)
        assert accepted.ok, accepted.text()
        held[0].fulfill(status=accepted.status, json=accepted.json())
        _wait_for_turns(page, 2)
        assert len(inspect(stack)["captures"]) == len(before) + 1
    else:
        held[0].abort()
    assert page.request.delete(f"{stack.portal}/api/v1/conversations/{cid}").ok
    maintenance(stack)
    assert {r["id"] for r in inspect(stack)["captures"]} == before
    assert inspect(stack)["reserved"] == 0


def test_two_tabs_compete_for_one_browser_capture(logged_in, stack):
    page = logged_in
    uploaded = page.request.post(
        stack.portal + "/api/v1/files?disposition=copy",
        multipart={"upload": {"name": "tabs.pdf", "mimeType": "application/pdf", "buffer": (FIXTURES / "cjk.pdf").read_bytes()}},
    )
    assert uploaded.ok
    fid = uploaded.json()["id"]
    wait_ready(page, stack, fid)
    page.goto(stack.portal + "/?files=" + fid)
    page.get_by_role("radio", name="对话", exact=True).click()
    cid = _start_conversation(page, "原生收件验收", "test-office-vision")
    _wait_for_turns(page, 1)
    page.locator(".turn .attach-chip.open").first.click()
    _frame(page, "tabs.pdf").locator(".textLayer span").first.wait_for()
    competitor = page.context.new_page()
    try:
        competitor.goto(page.url)
        competitor.locator(".turn .attach-chip.open").first.click()
        _frame(competitor, "tabs.pdf").locator(".textLayer span").first.wait_for()
        held, publications = [], []
        page.context.route("**/client-calls/*/claim", lambda route: held.append(route))
        page.context.on("request", lambda request: publications.append(request.url) if request.url.endswith("/result") else None)
        _send(page, f"原生控制 {fid} pdf")
        end = time.monotonic() + 20
        while not held:
            assert time.monotonic() < end
            page.wait_for_timeout(50)
        # Each tab has its own JS realm/view_id. The losing tab submits an actual
        # claim for the same call; it must receive 409 and cannot encode pixels.
        url = held[0].request.url
        winner = page.request.post(url)
        loser = competitor.request.post(url)
        assert winner.ok and loser.status == 409
        held[0].fulfill(status=200, json=winner.json())
        for route in held[1:]:
            route.fulfill(status=409, json=loser.json())
        page.context.unroute("**/client-calls/*/claim")
        _wait_for_turns(page, 2)
        assert len(publications) == 1
        captures = [r for r in inspect(stack)["captures"] if r["key"].startswith("derived/" + fid + "/")]
        assert len(captures) == 1
    finally:
        page.context.unroute("**/client-calls/*/claim")
        competitor.close()
        assert page.request.delete(f"{stack.portal}/api/v1/conversations/{cid}").ok
        maintenance(stack)


def test_two_users_prepare_independently_and_source_deletion_does_not_cancel_the_other(logged_in, stack):
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    page = logged_in
    username, password = "document-" + uuid.uuid4().hex[:12], uuid.uuid4().hex
    created = page.request.post(
        stack.portal + "/api/v1/admin/users", data={"username": username, "password": password, "display_name": "Document contender"}
    )
    assert created.ok, created.text()
    owner_token = next(c["value"] for c in page.context.cookies() if c["name"] == "ailab_token")
    with httpx.Client(base_url=stack.api, timeout=60) as login:
        response = login.post("/v1/auth/login", json={"username": username, "password": password})
        response.raise_for_status()
        other_token = response.json()["token"]
    with (
        httpx.Client(base_url=stack.api, headers={"Authorization": "Bearer " + owner_token}, timeout=60) as first,
        httpx.Client(base_url=stack.api, headers={"Authorization": "Bearer " + other_token}, timeout=60) as second,
    ):
        files = []
        for client in (first, second):
            response = client.post("/v1/files", files={"upload": ("contender.docx", (FIXTURES / "report.docx").read_bytes())})
            response.raise_for_status()
            fid = response.json()["id"]
            end = time.monotonic() + 60
            while client.get("/v1/files/" + fid).json()["status"] != "ready":
                assert time.monotonic() < end
                time.sleep(0.2)
            files.append(fid)
        assert first.get("/v1/files/" + files[1]).status_code == 404
        assert second.get("/v1/files/" + files[0]).status_code == 404
        bodies = [{"file_ids": [fid], "model": "test-office-vision", "mode": "chat", "client_send_id": uuid.uuid4().hex} for fid in files]
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda pair: pair[0].post("/v1/chat/prepare", json=pair[1]), zip((first, second), bodies)))
        assert all(r.status_code in {200, 409} for r in replies), [r.text for r in replies]
        assert first.delete("/v1/files/" + files[0]).is_success
        rejected = first.post("/v1/chat/prepare", json=bodies[0])
        assert rejected.status_code == 400 and rejected.json()["detail"] == "unknown file_id: " + files[0]
        end = time.monotonic() + 90
        while True:
            ready = second.post("/v1/chat/prepare", json=bodies[1])
            if ready.status_code == 200:
                break
            assert ready.status_code == 409 and time.monotonic() < end, ready.text
            time.sleep(0.25)
        end = time.monotonic() + 20
        while True:
            state = inspect(stack)
            if state["active"] == state["reserved"] == 0:
                break
            assert time.monotonic() < end, state
            time.sleep(0.25)
        assert second.get("/v1/files/" + files[1]).json()["status"] == "ready"


def test_process_death_after_storage_write_recovers_its_capacity(logged_in, stack):
    page = logged_in
    uploaded = page.request.post(
        stack.portal + "/api/v1/files?disposition=copy",
        multipart={"upload": {"name": "crash.pdf", "mimeType": "application/pdf", "buffer": (FIXTURES / "cjk.pdf").read_bytes()}},
    )
    assert uploaded.ok
    fid = uploaded.json()["id"]
    wait_ready(page, stack, fid)
    script = """
import os,sys
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject
from ailab_platform.api.owned_assets import publish
with SessionLocal() as db:
    file=db.get(FileObject,sys.argv[1])
    publish(db,file.user_id,'derived/'+file.id+'/crash-after-write',b'partial-before-business-binding',file.sha256,
        kind='capture',content_type='application/octet-stream',validate=lambda:None,bind=lambda _:os._exit(9))
"""
    result = subprocess.run([sys.executable, "-c", script, fid], cwd=PLATFORM, env=stack.env, timeout=30, capture_output=True)
    assert result.returncode == 9, result.stderr.decode()
    key = "derived/" + fid + "/crash-after-write"
    physical = Path(stack.env["AILAB_STORAGE_ROOT"]) / key
    assert physical.read_bytes() == b"partial-before-business-binding"
    assert inspect(stack)["reserved"] == physical.stat().st_size
    recovery = """
import sys
from datetime import UTC,datetime,timedelta
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import DerivedAsset
from ailab_platform.api.derived_assets import collect_batch
from ailab_platform.storage import get_storage
key=sys.argv[1]
with SessionLocal() as db:
    collect_batch(db,get_storage(),transient_only=True)
    assert db.get(DerivedAsset,key).state=='writing'
    due=datetime.now(UTC)+timedelta(seconds=settings.derived_write_lease_seconds+settings.derived_orphan_grace_seconds+1)
    collect_batch(db,get_storage(),now=due,transient_only=True)
    assert db.get(DerivedAsset,key) is None
"""
    subprocess.run([sys.executable, "-c", recovery, key], cwd=PLATFORM, env=stack.env, check=True, timeout=30, capture_output=True)
    assert not physical.exists()
    assert inspect(stack)["reserved"] == 0
