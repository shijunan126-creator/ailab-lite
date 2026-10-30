"""Browser calls are fenced, claimed once, bounded and recoverable."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.agent import discovery
from ailab_platform.agent.loop import _tools_for_run
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import ClientCall, Run, RunEvent
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import owned


def create(api, rid, headers, call_id="call", payload=None):  # noqa: F811
    return api.post(
        f"/internal/runs/{rid}/client-calls",
        headers=headers,
        json={
            "call_id": call_id,
            "kind": "js",
            "payload": payload or {"code": "1+1"},
        },
    )


def path(rid, call_id="call"):
    return f"/v1/runs/{rid}/client-calls/{call_id}"


def test_claim_result_and_replay(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert create(api, rid, headers).status_code == 200
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: api.post(path(rid) + "/claim", headers=AUTH), range(2)))
    assert sorted(r.status_code for r in claims) == [200, 409]
    token = next(r.json()["claim_token"] for r in claims if r.status_code == 200)
    body = {"claim_token": token, "ok": True, "value": "2", "logs": "", "stats": {"ms": 2}}
    assert api.post(path(rid) + "/result", headers=AUTH, json=body).status_code == 200
    assert api.post(path(rid) + "/result", headers=AUTH, json=body).status_code == 409
    result = create(api, rid, headers).json()
    assert result["result"] == {"ok": True, "value": "2", "logs": "", "ms": 2, "source": "browser"}
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type.in_(["client.call", "client.result"])).order_by(RunEvent.seq)
            )
        )
        assert [e.type for e in events] == ["client.call", "client.result"]


def test_expiry_limits_and_cancel(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert create(api, rid, headers, payload={"code": "a" * (settings.client_js_code_max_bytes + 1)}).status_code == 413
    assert create(api, rid, headers, payload={"code": "a", "input": "a" * settings.client_js_input_max_bytes}).status_code == 413
    assert create(api, rid, headers).status_code == 200
    with SessionLocal() as db:
        db.get(ClientCall, (rid, "call")).claim_deadline = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    state = api.get(f"/internal/runs/{rid}/client-calls/call", headers=SERVICE).json()
    assert state["status"] == "expired" and "没有在线" in state["result"]["error"]["message"]
    assert api.post(path(rid) + "/claim", headers=AUTH).status_code == 409
    assert create(api, rid, headers, "other").status_code == 200
    token = api.post(path(rid, "other") + "/claim", headers=AUTH).json()["claim_token"]
    body = {"claim_token": token, "ok": True, "value": "x" * (settings.client_js_value_max_bytes + 1), "stats": {"ms": 1}}
    assert api.post(path(rid, "other") + "/result", headers=AUTH, json=body).status_code == 413
    with SessionLocal() as db:
        db.get(ClientCall, (rid, "other")).deadline = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    body["value"] = "2"
    assert api.post(path(rid, "other") + "/result", headers=AUTH, json=body).status_code == 409
    assert "超时" in api.get(f"/internal/runs/{rid}/client-calls/other", headers=SERVICE).json()["result"]["error"]["message"]
    assert create(api, rid, headers, "cancel").status_code == 200
    assert api.post(f"/v1/runs/{rid}/cancel", headers=AUTH).status_code == 200
    with SessionLocal() as db:
        assert db.get(ClientCall, (rid, "cancel")).status == "canceled"


def test_discovery_only_for_portal():
    portal = _tools_for_run({"options": {"client": "portal", "mode": "chat"}})
    assert "run_js" in [x["name"] for x in discovery.search("计算", "tool", 5, portal)]
    assert "run_js" not in [x["function"]["name"] for x in discovery.offered(portal, [])]
    assert "run_js" not in [x["function"]["name"] for x in _tools_for_run({"options": {"mode": "work"}})]


def test_agent_waits_inline_and_reuses_result(api, monkeypatch):  # noqa: F811
    import asyncio

    from ailab_platform.agent.loop import AgentLoop, _Turn
    from ailab_platform.llm import ToolCall

    rid, _, headers = owned(api)
    call_id = f"{rid}:3:0"

    class Platform:
        async def create_client_call(self, run_id, payload):
            response = api.post(f"/internal/runs/{run_id}/client-calls", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        async def get_client_call(self, run_id, identity):
            return api.get(f"/internal/runs/{run_id}/client-calls/{identity}", headers=SERVICE).json()

        async def get_run_state(self, run_id):
            return {"status": api.get(f"/v1/runs/{run_id}", headers=AUTH).json()["status"]}

    loop = AgentLoop(platform=Platform())
    loop._states[rid] = {"tool_log": {}}

    async def checkpoint(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", checkpoint)
    turn = _Turn({"options": {"client": "portal", "mode": "chat"}})
    call = ToolCall("upstream", "run_js", {"code": "1+1"})

    async def execute():
        task = asyncio.create_task(loop._durable_tool(turn, rid, call, 3, "3:0"))
        for _ in range(30):
            await asyncio.sleep(0.01)
            claim = api.post(path(rid, call_id) + "/claim", headers=AUTH)
            if claim.status_code == 200:
                break
        assert claim.status_code == 200
        body = {"claim_token": claim.json()["claim_token"], "ok": True, "value": "2", "stats": {"ms": 1}}
        assert api.post(path(rid, call_id) + "/result", headers=AUTH, json=body).status_code == 200
        result = await task
        assert result["value"] == "2"
        # An interrupted agent has a started checkpoint but no recorded result.
        assert await loop._durable_tool(turn, rid, call, 3, "3:0") == result

    asyncio.run(execute())
    with SessionLocal() as db:
        assert len(list(db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "client.call")))) == 1


def test_agent_unclaimed_and_cancel(api, monkeypatch):  # noqa: F811
    import asyncio

    from ailab_platform.agent import loop as agent_loop
    from ailab_platform.agent.loop import AgentLoop, RunCanceled, _Turn
    from ailab_platform.llm import ToolCall

    monkeypatch.setattr(settings, "client_js_claim_timeout_seconds", 0.05)
    monkeypatch.setattr(agent_loop, "CANCEL_POLL_SECONDS", 0.05)
    rid, _, headers = owned(api)

    class Platform:
        async def create_client_call(self, run_id, payload):
            return api.post(f"/internal/runs/{run_id}/client-calls", headers=headers, json=payload).json()

        async def get_client_call(self, run_id, identity):
            return api.get(f"/internal/runs/{run_id}/client-calls/{identity}", headers=SERVICE).json()

        async def get_run_state(self, run_id):
            return {"status": api.get(f"/v1/runs/{run_id}", headers=AUTH).json()["status"]}

    loop = AgentLoop(platform=Platform())
    loop._states[rid] = {"tool_log": {}}

    async def checkpoint(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", checkpoint)
    turn = _Turn({"options": {"client": "portal", "mode": "chat"}})
    call = ToolCall("upstream", "run_js", {"code": "1"})

    async def execute():
        result = await asyncio.wait_for(loop._durable_tool(turn, rid, call, 1, "1:0"), 2)
        assert "没有在线" in result["error"]["message"]
        monkeypatch.setattr(settings, "client_js_claim_timeout_seconds", 6)
        task = asyncio.create_task(loop._durable_tool(turn, rid, call, 2, "2:0"))
        await asyncio.sleep(0.05)
        assert api.post(f"/v1/runs/{rid}/cancel", headers=AUTH).status_code == 200
        try:
            await asyncio.wait_for(task, 1)
        except RunCanceled:
            return
        raise AssertionError("cancel was not observed")

    asyncio.run(execute())


def test_owner_and_result_limits(api):  # noqa: F811
    from ailab_platform.api.main import app
    from ailab_platform.deps import current_user
    from ailab_platform.models import User

    rid, _, headers = owned(api)
    assert create(api, rid, headers).status_code == 200
    app.dependency_overrides[current_user] = lambda: User(id="another-user")
    try:
        assert api.post(path(rid) + "/claim", headers=AUTH).status_code == 404
        assert api.post(path(rid) + "/result", headers=AUTH, json={"claim_token": "x", "ok": True, "stats": {"ms": 1}}).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)
    token = api.post(path(rid) + "/claim", headers=AUTH).json()["claim_token"]
    body = {"claim_token": token, "ok": False, "error": {"type": "runtime", "message": "x"}, "stats": {"ms": 1}}
    for field, value in [
        ("logs", "x" * (settings.client_js_logs_max_bytes + 1)),
        ("error", {"type": "runtime", "message": "x" * (settings.client_js_error_max_bytes + 1)}),
    ]:
        assert api.post(path(rid) + "/result", headers=AUTH, json={**body, field: value}).status_code == 413
    assert api.post(path(rid) + "/result", headers=AUTH, json={**body, "claim_token": "wrong"}).status_code == 409
    assert api.post(path(rid) + "/result", headers=AUTH, json={**body, "value": "not json"}).status_code == 422
    assert api.post(path(rid) + "/result", headers=AUTH, json=body).status_code == 200


def test_internal_creation_requires_live_attempt_and_interactive_run(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert create(api, rid, SERVICE).status_code == 409
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.options = {"mode": "work"}
        db.commit()
    assert create(api, rid, headers).status_code == 409
