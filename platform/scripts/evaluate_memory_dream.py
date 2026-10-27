#!/usr/bin/env python3
"""Offline evaluation for the nightly memory dream.

Runs the same episode/extract/judge path, but never applies a plan. Use it to compare prompts or
models before changing the nightly job for real users.

    PYTHONPATH=platform python platform/scripts/evaluate_memory_dream.py --username admin
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream.service import (
    _active,
    _episodes,
    _extract,
    _finish,
    _judge,
    _last_success,
    _memory_view,
    _new_runs,
    _screen,
    dream_call,
)
from ailab_platform.memory_recall.tombstones import tombstones_of
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
        active = _active(memory["entries"])
        episodes = _episodes(db, user, runs)
        if model:
            from ailab_platform.config import settings

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
            facts, usage, used_model = await _extract(episodes, active, user)
            db.refresh(user)
            facts = _screen(facts, tombstones_of(user))
            db.commit()
            verdicts, judge_usage = await _judge(facts, active, user, used_model) if facts else ({}, {})
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
            "candidates": len(facts),
            "duplicates": sum(len(v.get("duplicate_ids") or []) for v in verdicts.values()),
            "contradictions": sum(len(v.get("contradicted_ids") or []) for v in verdicts.values()),
            "facts": [fact["text"] for fact in facts],
            "token_usage": {
                key: int(usage.get(key, 0)) + int(judge_usage.get(key, 0))
                for key in ("prompt_tokens", "completion_tokens", "cached_tokens")
            },
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
