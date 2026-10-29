"""Opt-in real-model compaction check on synthetic data and an isolated API-owned ledger.

No user files, production database or sandbox execution is used. Credentials are loaded from
an explicitly supplied local env file and never copied into evidence.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path


def configure(args):
    from dotenv import dotenv_values

    args.state_dir.mkdir(parents=True, exist_ok=True)
    for name, value in dotenv_values(args.key_env_file).items():
        if name.startswith("AILAB_KEY_") and value:
            os.environ[name] = value
    os.environ.update(
        AILAB_ENV="test",
        AILAB_ENV_FILE=str(args.state_dir / "no.env"),
        AILAB_PROFILE="test",
        AILAB_DATA_DIR=str(args.state_dir),
        AILAB_DATABASE_URL="sqlite:///" + str(args.state_dir / "verification.db"),
        AILAB_STORAGE_ROOT=str(args.state_dir / "objects"),
        AILAB_QUEUE_BACKEND="memory",
        AILAB_DEV_TOKEN="context-check-token",
        AILAB_SERVICE_TOKEN="context-check-service",
        AILAB_MODELS_FILE=str(args.models_file),
        AILAB_LLM_MAX_RETRIES="0",
        AILAB_LLM_TIMEOUT_SECONDS="90",
        AILAB_LLM_TOTAL_TIMEOUT_SECONDS="120",
        AILAB_MODEL_MAX_OUTPUT_TOKENS="2048",
    )


async def verify(args):
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from ailab_platform import catalog as catalog_module
    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.agent.loop import AgentLoop, _Turn
    from ailab_platform.api.main import app
    from ailab_platform.catalog import Catalog
    from ailab_platform.db import SessionLocal
    from ailab_platform.llm import OpenAICompatibleClient
    from ailab_platform.models import Conversation, ModelUsage, Run
    from ailab_platform.run_state import transition_run

    catalog = catalog_module.get_catalog()
    model = args.model or catalog.default_id()
    original = catalog.get(model)
    if not original or original.provider != "openai" or not original.available:
        raise RuntimeError("real model unavailable")
    # A smaller local verification window exercises compaction without huge requests.
    spec = replace(original, context_tokens=8192, max_output_tokens=2048, fallback=[], disable_reasoning_for_quiet=True)
    catalog = Catalog([spec])
    from unittest.mock import patch

    patches = [
        patch("ailab_platform." + module + ".get_catalog", return_value=catalog)
        for module in ("catalog", "agent.loop", "llm", "quota", "api.routes_runs")
    ]
    for item in patches:
        item.start()
    auth, service = {"Authorization": "Bearer context-check-token"}, {"X-Service-Token": "context-check-service"}

    class MeasuredModel(OpenAICompatibleClient):
        inputs = []

        async def chat_stream(self, messages, *a, **kw):
            if len(self.inputs) >= 6:
                raise RuntimeError("verification call cap reached")
            self.inputs.append(messages.copy())
            return await super().chat_stream(messages, *a, **kw)

    llm = MeasuredModel(
        spec.base_url, spec.api_key(), spec.upstream_model, echo_reasoning=spec.echo_reasoning, disable_reasoning_for_quiet=True
    )
    with TestClient(app) as client:

        def request(method, url, payload=None, attempt=None):
            response = client.request(method, url, headers={**service, **({"X-Run-Attempt": attempt} if attempt else {})}, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"verification API {response.status_code}: {response.text[:200]}")
            return response.json()

        class Bridge:
            def __init__(self):
                self.attempt = None

            async def reserve_usage(self, body):
                return request("POST", "/internal/usage/reserve", body)

            async def settle_usage(self, body):
                return request("POST", "/internal/usage/settle", body)

            async def append_event(self, rid, kind, body=None):
                return request("POST", f"/internal/runs/{rid}/events", {"type": kind, "payload": body or {}}, self.attempt)

            async def get_run_state(self, rid):
                return request("GET", f"/internal/runs/{rid}/state")

            async def get_model_context(self, rid, after=None):
                return request("GET", f"/internal/runs/{rid}/model-context" + ("?after=" + after if after else ""))

            async def save_model_summary(self, rid, body):
                return request("PUT", f"/internal/runs/{rid}/model-summary", body, self.attempt)

            async def put_checkpoint(self, rid, body):
                return request("PUT", f"/internal/runs/{rid}/checkpoint", body, self.attempt)

        bridge = Bridge()
        loop = AgentLoop(bridge, object(), llm)
        conv_response = client.post("/v1/conversations", headers=auth, json={})
        conv_response.raise_for_status()
        conversation = conv_response.json()["id"]
        parent, history_ids = None, []

        def seed_batch(number):
            nonlocal parent
            with SessionLocal() as db:
                from ailab_platform.models import User

                user = db.scalar(select(User))
                for index in range(8):
                    text = (
                        "项目代号 AL-219，交付格式只允许 XLSX，禁止自动发送邮件。"
                        if number == 0 and index == 0
                        else "更正：截止日改为2031-06-18，旧日期2031-06-10作废。"
                        if number == 1 and index == 0
                        else f"进度记录 {number}-{index}：核对资料后继续处理，尚有表格格式待完成。"
                    )
                    row = Run(
                        user_id=user.id,
                        conversation_id=conversation,
                        parent_run_id=parent,
                        prompt=text,
                        summary="已收到，继续按已确认约束处理。",
                        status="succeeded",
                        model=model,
                        options={"mode": "chat", "capability_discovery": 1},
                    )
                    db.add(row)
                    db.flush()
                    parent = row.id
                    history_ids.append(row.id)
                db.get(Conversation, conversation).active_run_id = parent
                db.commit()

        async def admitted_run(prompt):
            nonlocal parent
            response = client.post(
                f"/v1/conversations/{conversation}/messages", headers=auth, json={"prompt": prompt, "model": model, "mode": "chat"}
            )
            response.raise_for_status()
            run = response.json()
            claimed = request("POST", f"/internal/jobs/{run['id']}/claim", {"consumer": "real-context-verification"})
            bridge.attempt = claimed["attempt_id"]
            with SessionLocal() as db:
                transition_run(db, run["id"], "running")
            run.update(
                _attempt_id=bridge.attempt,
                _ledger_tokens=0,
                options={"mode": "chat", "capability_discovery": 1},
                context_settings={"auto_compact": True, "threshold_ratio": 0.01},
            )
            return run

        summaries = []
        for number in range(2):
            seed_batch(number)
            run = await admitted_run("继续处理，并保留已确认约束")
            messages = await loop._assemble_context(
                run["id"], run, model, [{"role": "system", "content": "遵守用户约束"}, {"role": "user", "content": run["prompt"]}]
            )
            summaries.append(next(m["content"] for m in messages if str(m.get("content", "")).startswith("历史摘要")))
            request("POST", f"/internal/runs/{run['id']}/status", {"status": "succeeded", "summary": "继续处理"}, bridge.attempt)
            parent = run["id"]
        summary_inputs = [json.loads(messages[1]["content"]) for messages in llm.inputs[:2]]
        assert summary_inputs[0]["summary"] == ""
        assert summary_inputs[1]["summary"]
        assert not ({t["id"] for t in summary_inputs[0]["turns"]} & {t["id"] for t in summary_inputs[1]["turns"]})
        assert all(word in summaries[-1] for word in ("AL-219", "XLSX", "邮件", "2031-06-18"))

        run = await admitted_run("仅输出XLSX，不发送邮件；完成后交付。")
        state = fresh_state()
        state["messages"] = [{"role": "system", "content": "按约束完成任务"}, {"role": "user", "content": run["prompt"]}]
        state["user_message_index"] = 1
        state["tool_log"] = {"confirmed": {"name": "exec", "started": True, "result": {"ok": True, "exit_code": 0}}}
        state["delivered"] = {"/workspace/outputs/confirmed.xlsx": 88}
        turn = _Turn(run)
        turn.messages = state["messages"]
        turn.delivered = state["delivered"]
        loop._states[run["id"]], loop._steps[run["id"]] = state, 0
        for generation in range(2):
            for index in range(5 if generation == 0 else 3):
                call = f"tool-{generation}-{index}"
                text = "status pending; table formatting not completed. " * 100
                if generation == 0 and index == 1:
                    middle = len(text) // 2
                    text = text[:middle] + "唯一核验码 ZX-7419，必须保留。" + text[middle:]
                turn.messages.extend(
                    [
                        {
                            "role": "assistant",
                            "tool_calls": [{"id": call, "type": "function", "function": {"name": "exec", "arguments": "{}"}}],
                        },
                        {"role": "tool", "tool_call_id": call, "content": text},
                    ]
                )
            await loop._compact_work_context(run["id"], turn, llm, model, [])
        assert "ZX-7419" in state["compaction"]["summary"]
        saved = request("GET", f"/internal/runs/{run['id']}/checkpoint")
        assert saved["state"]["delivered"] == state["delivered"]
        assert saved["state"]["tool_log"]["confirmed"]["result"]["compacted"] is True
        with SessionLocal() as db:
            rows = list(db.scalars(select(ModelUsage)))
            assert len(rows) == len(llm.inputs) and len({row.call_id for row in rows}) == len(rows)
            result = {
                "model": model,
                "actual_backends": sorted({row.actual_backend for row in rows}),
                "calls": len(rows),
                "prompt_tokens": sum(row.prompt_tokens for row in rows),
                "completion_tokens": sum(row.completion_tokens for row in rows),
                "checks": {
                    "old_summary_plus_only_new_delta": True,
                    "corrected_constraint_retained": True,
                    "middle_of_tool_output_retained": True,
                    "checkpoint_confirmed_effects_retained": True,
                },
                "summaries": summaries,
                "run_summary": state["compaction"]["summary"],
                "scope": "synthetic constraints; two cross-run and two current-run compactions; no user data or sandbox execution",
            }
    for item in reversed(patches):
        item.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"summaries", "run_summary"}}, ensure_ascii=False))


def audit_existing(args):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from ailab_platform.api.main import app
    from ailab_platform.api.routes_runtime import read_checkpoint
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import ContextSummaryFrame, ModelUsage, Run

    with TestClient(app), SessionLocal() as db:
        if args.check_pinned_existing:
            from dataclasses import replace
            from unittest.mock import patch

            from ailab_platform.agent.loop import AgentLoop, _Turn
            from ailab_platform.catalog import Catalog, get_catalog
            from ailab_platform.llm import llm_for

            run = db.scalar(select(Run).where(Run.checkpoint_step > 0))
            checkpoint = read_checkpoint(db, run)
            state = checkpoint["state"]
            spec = replace(get_catalog().get(run.model), context_tokens=8192, max_output_tokens=2048, disable_reasoning_for_quiet=True)
            catalog = Catalog([spec])

            class Bridge:
                def __init__(self, client):
                    self.client = client

                async def reserve_usage(self, body):
                    return self.send("POST", "/internal/usage/reserve", body)

                async def settle_usage(self, body):
                    return self.send("POST", "/internal/usage/settle", body)

                async def append_event(self, rid, kind, body=None):
                    return self.send("POST", f"/internal/runs/{rid}/events", {"type": kind, "payload": body or {}})

                async def get_run_state(self, rid):
                    return self.send("GET", f"/internal/runs/{rid}/state")

                async def put_checkpoint(self, rid, body):
                    return self.send("PUT", f"/internal/runs/{rid}/checkpoint", body)

                def send(self, method, path, body=None):
                    r = self.client.request(
                        method,
                        path,
                        json=body,
                        headers={"X-Service-Token": "context-check-service", "X-Run-Attempt": checkpoint["attempt_id"]},
                    )
                    r.raise_for_status()
                    return r.json()

            from fastapi.testclient import TestClient as Client

            with (
                Client(app) as client,
                patch("ailab_platform.agent.loop.get_catalog", return_value=catalog),
                patch("ailab_platform.llm.get_catalog", return_value=catalog),
            ):
                claimed = client.post(
                    f"/internal/jobs/{run.id}/claim",
                    headers={"X-Service-Token": "context-check-service"},
                    json={"consumer": "pinned-state-verification"},
                )
                claimed.raise_for_status()
                owner = claimed.json()
                if owner.get("state") == "busy":
                    from ailab_platform.models import RunAttempt

                    owner["attempt_id"] = db.scalar(select(RunAttempt.id).where(RunAttempt.run_id == run.id, RunAttempt.ended_at.is_(None)))
                elif owner.get("state") != "claimed":
                    raise RuntimeError("synthetic run unavailable; no inference dispatched")
                checkpoint["attempt_id"] = owner["attempt_id"]
                loop = AgentLoop(Bridge(client), object(), llm_for(spec.id))
                turn = _Turn(
                    {
                        "prompt": run.prompt,
                        "_attempt_id": owner["attempt_id"],
                        "_ledger_tokens": 0,
                        "options": {"mode": "chat", "capability_discovery": 1},
                    }
                )
                turn.messages, turn.delivered = state["messages"], state["delivered"]
                loop._states[run.id], loop._steps[run.id] = state, checkpoint["step"]
                for index in range(3):
                    call = f"pinned-{index}"
                    turn.messages.extend(
                        [
                            {
                                "role": "assistant",
                                "tool_calls": [{"id": call, "type": "function", "function": {"name": "exec", "arguments": "{}"}}],
                            },
                            {"role": "tool", "tool_call_id": call, "content": "status pending; table formatting not completed. " * 100},
                        ]
                    )
                db.commit()
                asyncio.run(loop._compact_work_context(run.id, turn, loop.llm, spec.id, []))
                assert "confirmed.xlsx" in state["compaction"]["summary"]
                db.expire_all()
        frames = list(db.scalars(select(ContextSummaryFrame).order_by(ContextSummaryFrame.created_at)))
        assert len(frames) == 2 and frames[1].from_run_id == frames[0].through_run_id
        summaries = [frame.summary for frame in frames]
        assert all(word in summaries[-1] for word in ("AL-219", "XLSX", "邮件", "2031-06-18"))
        run = db.scalar(select(Run).where(Run.checkpoint_step > 0))
        saved = read_checkpoint(db, run)["state"]
        assert "ZX-7419" in saved["compaction"]["summary"]
        assert saved["delivered"] == {"/workspace/outputs/confirmed.xlsx": 88}
        record = saved["tool_log"]["confirmed"]
        assert record["name"] == "exec" and record["started"] and record["result"]["compacted"]
        rows = list(db.scalars(select(ModelUsage)))
        assert 4 <= len(rows) <= 6 and len({row.call_id for row in rows}) == len(rows)
        result = {
            "model": rows[0].alias,
            "actual_backends": sorted({row.actual_backend for row in rows}),
            "calls": len(rows),
            "prompt_tokens": sum(row.prompt_tokens for row in rows),
            "completion_tokens": sum(row.completion_tokens for row in rows),
            "checks": {
                "old_summary_plus_only_new_delta": True,
                "corrected_constraint_retained": True,
                "middle_of_tool_output_retained": True,
                "checkpoint_confirmed_effects_retained": True,
            },
            "summaries": summaries,
            "run_summary": saved["compaction"]["summary"],
            "scope": f"two cross-run and {len(rows) - 2} current-run DeepSeek compactions; isolated API ledger and persisted checkpoints",
            "pinned_program_state_checked": "confirmed.xlsx" in saved["compaction"]["summary"],
            "audit_note": "Stored checkpoints and API ledger audited; one extra compaction checked authoritative delivery state.",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"summaries", "run_summary"}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-pinned-existing", action="store_true", help="one real compaction of the existing synthetic checkpoint")
    parser.add_argument("--audit-existing", action="store_true", help="validate existing synthetic evidence without inference")
    parser.add_argument("--execute", action="store_true", help="explicitly permit metered real-model calls")
    parser.add_argument("--models-file", type=Path, required=True)
    parser.add_argument("--key-env-file", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute and not args.audit_existing:
        parser.error("--execute is required")
    configure(args)
    if args.check_pinned_existing and not args.execute:
        parser.error("--check-pinned-existing requires --execute")
    if args.audit_existing:
        audit_existing(args)
    else:
        asyncio.run(verify(args))
