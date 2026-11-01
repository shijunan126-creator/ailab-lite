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


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(any(r["missed"] or r["harmful"] for r in result["results"]))
