#!/usr/bin/env python3
"""Offline evaluation for the nightly memory dream.

Runs the same harvest/model/validation path, but never applies the plan. Use it to compare
prompts or models before enabling the nightly job for a user.

    PYTHONPATH=platform python platform/scripts/evaluate_memory_dream.py --username admin
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream.service import (
    _call_model,
    _digest,
    _last_success,
    _memory_view,
    _messages,
    _new_runs,
    _validate,
)
from ailab_platform.models import User


async def evaluate(username: str | None, user_id: str | None, model: str | None) -> dict:
    with SessionLocal() as db:
        user = db.get(User, user_id) if user_id else db.scalar(select(User).where(User.username == username))
        if not user:
            raise SystemExit("user not found")
        last = _last_success(db, user.id)
        runs = _new_runs(db, user.id, last, datetime.now(UTC))
        if not runs:
            return {"user": user.id, "runs": 0, "changes": 0, "note": "no new runs"}
        memory = _memory_view(user)
        digest = _digest(runs, 100_000)
        prompt = _messages(memory, runs)
        if model:
            from ailab_platform.config import settings

            settings.memory_dream_model = model
        plan, usage = await _call_model(memory, runs, user)
        plan = _validate(plan, memory, {r.id for r in runs})
        return {
            "user": user.id,
            "runs": len(runs),
            "input_chars": len(digest),
            "prompt_chars": sum(len(m["content"]) for m in prompt),
            "changes": len(plan.changes),
            "ops": {op: sum(1 for chance in plan.changes if chance.op == op) for op in ("add", "update", "noop")},
            "confidence": [c.confidence for c in plan.changes],
            "journal": plan.journal,
            "token_usage": usage,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    parser.add_argument("--user-id")
    parser.add_argument("--model")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.username, args.user_id, args.model)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
