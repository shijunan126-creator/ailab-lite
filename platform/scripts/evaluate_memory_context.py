#!/usr/bin/env python3
"""Deterministic memory-context golden evaluation, without secrets, models or a database.

This measures recall and harmful *context injection*, not end-to-end answer quality. Optional
paired model answers can be scored separately; never confuse context precision with model accuracy.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ailab_platform.memory_recall.policy import model_view


def evaluate(path: Path | None = None) -> dict:
    corpus = json.loads((path or Path(__file__).resolve().parents[1] / "tests/golden/memory.json").read_text())
    results = []
    for case in corpus["cases"]:
        selected = model_view(
            {"entries": corpus["entries"], "enabled": case.get("enabled", True)},
            case.get("project_id"),
            mode=case.get("memory_mode", "shared"),
            query=case["query"],
            as_of=datetime.fromisoformat(case["as_of"]) if case.get("as_of") else None,
        )["entries"]
        ids = {e["id"] for e in selected}
        required = set(case.get("required", []))
        forbidden = set(case.get("forbidden", []))
        results.append(
            {
                "name": case["name"],
                "selected": sorted(ids),
                "missed": sorted(required - ids),
                "harmful": sorted(forbidden & ids),
                "baseline_missed": len(required),
            }
        )
    return {
        "cases": len(results),
        "results": results,
        "required_recall": 1 - sum(len(r["missed"]) for r in results) / max(1, sum(r["baseline_missed"] for r in results)),
        "context_hurt_rate": sum(bool(r["harmful"]) for r in results) / len(results),
        "no_memory_required_recall": 0.0,
        "answer_quality_measured": False,
    }


def evaluate_v2(path=None):
    """Synthetic lexical/freshness checks; trigger fixtures require actual model tool decisions."""
    from datetime import UTC
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ailab_platform.contracts.memory import MemoryOperation
    from ailab_platform.memory_recall.policy import visible
    from ailab_platform.memory_recall.retrieval import search_memory
    from ailab_platform.models import Base, Conversation, Run, User

    dataset = json.loads((path or Path(__file__).resolve().parents[1] / "tests/golden/memory_v2.json").read_text())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    scores, unrelated, details = [], [], []
    with Session(engine) as db:
        user = User(id="usr_eval", email="synthetic@example.invalid")
        db.add(user)
        db.flush()
        for index, item in enumerate(dataset["history"]):
            conv = Conversation(id=f"conv_eval_{index}", user_id=user.id, title="合成对话")
            db.add(conv)
            db.flush()
            db.add(
                Run(
                    id=item["id"],
                    user_id=user.id,
                    conversation_id=conv.id,
                    model="local-demo",
                    status="succeeded",
                    prompt=item["prompt"],
                    summary=item["summary"],
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
        db.flush()
        document = {"revision": 1, "entries": dataset["items"]}
        for case in dataset["retrieval"]:
            result = search_memory(
                db,
                user,
                SimpleNamespace(conversation_id=None),
                document,
                MemoryOperation(action="search", query=case["query"]),
                None,
                "shared",
            )
            ids = [r.get("id", r.get("run_id")) for r in result["results"]]
            expected = set(case["expected"])
            if expected:
                recall = len(expected & set(ids)) / len(expected)
                rank = next((i + 1 for i, key in enumerate(ids) if key in expected), None)
                scores.append((recall, 1 / rank if rank else 0))
            else:
                unrelated.append(not ids)
            details.append({"query": case["query"], "selected": ids, "expected": sorted(expected)})
    engine.dispose()
    freshness = [not visible({"status": "active", **c["old"]}) and visible({"status": "active", **c["new"]}) for c in dataset["freshness"]]
    return {
        "recall_at_5": sum(s[0] for s in scores) / len(scores),
        "mrr": sum(s[1] for s in scores) / len(scores),
        "abstain_rate": sum(unrelated) / len(unrelated),
        "retrieval_cases": len(scores),
        "unrelated_cases": len(unrelated),
        "freshness_correct": sum(freshness),
        "freshness_cases": len(freshness),
        "details": details,
        "trigger_fixture_cases": len(dataset["trigger"]),
        "trigger_evaluated": False,
        "real_model_evaluated": False,
    }


if __name__ == "__main__":
    import sys

    result = evaluate_v2() if "--v2" in sys.argv else evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(any(r["missed"] or r["harmful"] for r in result.get("results", [])))
