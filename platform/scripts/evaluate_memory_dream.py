#!/usr/bin/env python3
"""Offline evaluation for the nightly memory dream.

Runs the same single document rewrite call, but never applies a plan. Use it to compare prompts or
models before changing the nightly job for real users.

    PYTHONPATH=platform python platform/scripts/evaluate_memory_dream.py --username admin
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.catalog import get_catalog
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream.document import apply_ops, messages
from ailab_platform.memory_dream.service import (
    _active,
    _chat,
    _episodes,
    _finish,
    _fit_episodes,
    _json,
    _last_success,
    _memory_view,
    _new_runs,
    dream_call,
)
from ailab_platform.memory_recall.policy import project_mode, run_project
from ailab_platform.models import MemoryDreamRun, User, new_id


async def evaluate(username: str | None, user_id: str | None, model: str | None) -> dict:
    with SessionLocal() as db:
        user = db.get(User, user_id) if user_id else db.scalar(select(User).where(User.username == username))
        if not user:
            raise SystemExit("user not found")
        runs = _new_runs(db, user.id, _last_success(db, user.id), datetime.now(UTC))
        if not runs:
            return {"user": user.id, "runs": 0, "facts": 0, "note": "no new runs"}
        memory = _memory_view(user)
        pid = run_project(db, runs[0])
        runs = [r for r in runs if run_project(db, r) == pid]
        scope = pid if project_mode(db, pid) == "isolated" else None
        active = [e for e in _active(memory["entries"]) if e.get("project_id") == scope]
        episodes = _fit_episodes(_episodes(db, user, runs), settings.memory_dream_input_chars)
        if model:
            settings.memory_dream_model = model
        claim = new_id("claim")
        job = MemoryDreamRun(
            user_id=user.id,
            local_date="eval-" + new_id("eval")[-5:],
            timezone="UTC",
            trigger="evaluation",
            status="running",
            claim_token=claim,
            claimed_until=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(job)
        db.commit()  # Evaluation has the same short-transaction and per-call billing contract.
        context = dream_call.set((job.id, claim))
        try:
            used_model = settings.memory_dream_model or get_catalog().default_id()
            raw, usage = await _chat(messages(episodes, active), used_model, user)
            result = _json(raw)
            ignored = {x.get("run") for x in result.get("ignored_runs", []) if isinstance(x, dict)}
            _, operations = apply_ops(
                memory["entries"], active, [e for e in episodes if e.run_id not in ignored], result.get("ops", []), user, scope
            )
            _finish(db, job, "evaluated", journal="离线评估：未写入用户记忆")
        except BaseException:
            db.rollback()
            _finish(db, job, "failed", error="离线评估未完成")
            raise
        finally:
            dream_call.reset(context)
        return {
            "user": user.id,
            "model": used_model,
            "runs": len(runs),
            "episodes": len(episodes),
            "active_facts": len(active),
            "operations": len(operations),
            "added": sum(op["op"] == "add" for op in operations),
            "ignored_runs": len(ignored),
            "token_usage": usage,
        }


async def compare(username: str | None, user_id: str | None, baseline: str | None, candidate: str) -> dict:
    first = await evaluate(username, user_id, baseline)
    second = await evaluate(username, user_id, candidate)
    return {"baseline": first, "candidate": second}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    parser.add_argument("--user-id")
    parser.add_argument("--model")
    parser.add_argument("--compare-model", help="run the same input through a second model for an offline A/B")
    args = parser.parse_args()
    if args.compare_model:
        result = asyncio.run(compare(args.username, args.user_id, args.model, args.compare_model))
    else:
        result = asyncio.run(evaluate(args.username, args.user_id, args.model))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
