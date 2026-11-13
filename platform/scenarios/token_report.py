"""Compare scenario token use between versions: per scenario/turn medians, then their sums.

Run the scenarios against each version with AILAB_SCENARIO_REPORT pointing at a file per version and round, outside the
repository. Then:

    cd platform
    python -m scenarios.token_report compare before=/tmp/a/round1.jsonl,/tmp/a/round2.jsonl after=/tmp/b/round1.jsonl

The reports alone give each turn's prompt, cached and output tokens and model calls (from the public API). The first
request of a turn and the weighted cost are only in the ledger. Export them where the database is reachable, for
example inside the API container, with only SQLAlchemy needed:

    python -m scenarios.token_report runs /tmp/a/*.jsonl /tmp/b/*.jsonl > /tmp/run-ids.txt
    docker compose exec -T api sh -c 'cat > /tmp/token_report.py' < scenarios/token_report.py
    docker compose exec -T api python /tmp/token_report.py ledger "$AILAB_DATABASE_URL" < /tmp/run-ids.txt > /tmp/usage.json
    python -m scenarios.token_report compare before=... after=... --usage /tmp/usage.json

Turns paused by the account's quota (`interrupted`) are left out: they measure the account, not the prompts.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

MILLI = 1000  # ledger weighted_amount is in thousandths of a weighted token
METRICS = ("first", "calls", "prompt", "uncached", "completion", "weighted")


def _rows(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        with Path(path).open() as source:
            rows.extend(row for line in source if line.strip() and (row := json.loads(line))["status"] != "interrupted")
    return rows


def ledger(url: str, run_ids: list[str]) -> dict[str, list[list]]:
    """Each run's model calls in order: [quiet, prompt, cached, completion, weighted]. Read-only."""
    from sqlalchemy import bindparam, create_engine, text

    query = text(
        "select run_id, coalesce(quiet, false), prompt_tokens, cached_tokens, completion_tokens, coalesce(weighted_amount, 0)"
        " from model_usage where run_id in :ids order by run_id, created_at, id"
    ).bindparams(bindparam("ids", expanding=True))
    out: dict[str, list[list]] = {}
    engine = create_engine(url)
    try:
        with engine.connect() as db:
            for start in range(0, len(run_ids), 500):
                for run_id, *call in db.execute(query, {"ids": run_ids[start : start + 500]}):
                    out.setdefault(run_id, []).append([bool(call[0]), *(int(v or 0) for v in call[1:])])
    finally:
        engine.dispose()
    return out


def _measure(row: dict, usage: dict[str, list[list]] | None) -> dict:
    calls = (usage or {}).get(row.get("run_id") or "")
    if calls:
        main = [c for c in calls if not c[0]]  # quiet calls are titles and side checks
        prompt, cached, completion = (sum(c[i] for c in calls) for i in (1, 2, 3))
        return {
            "first": main[0][1] if main else None,
            "calls": len(main),
            "prompt": prompt,
            "uncached": prompt - cached,
            "completion": completion,
            "weighted": sum(c[4] for c in calls) / MILLI,
        }
    prompt = row.get("prompt_tokens") or 0
    return {
        "first": None,
        "calls": row.get("calls"),
        "prompt": prompt,
        "uncached": prompt - (row.get("cached_tokens") or 0),
        "completion": row.get("completion_tokens"),
        "weighted": None,
    }


def compare(versions: dict[str, list[dict]], usage: dict | None) -> tuple[list[str], dict[str, Counter]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for label, rows in versions.items():
        for row in rows:
            groups[(row["scenario"], row["turn"], label)].append(row)
    labels = list(versions)
    lines = ["scenario/turn".ljust(56) + " | ".join(f"{label}: ok " + " ".join(METRICS) for label in labels)]
    totals = {label: Counter() for label in labels}
    for scenario, turn in sorted({(s, t) for s, t, _ in groups}):
        cells = []
        for label in labels:
            group = groups.get((scenario, turn, label), [])
            measured = [_measure(row, usage) for row in group]
            ok = sum(row["status"] == "succeeded" for row in group)
            totals[label].update(ok=ok, n=len(group))
            medians = {}
            for metric in METRICS:
                values = [m[metric] for m in measured if m[metric] is not None]
                medians[metric] = round(statistics.median(values)) if values else None
                totals[label][metric] += medians[metric] or 0
            cells.append(f"{ok}/{len(group)} " + " ".join("-" if v is None else str(v) for v in medians.values()))
        lines.append(f"{scenario[:50]} t{turn}".ljust(56) + " | ".join(cells))
    return lines, totals


def _change(before: float, after: float) -> str:
    return f"{(after - before) / before:+.0%}" if before else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    runs = sub.add_parser("runs", help="print the run ids in reports, one per line")
    runs.add_argument("reports", nargs="+")
    led = sub.add_parser("ledger", help="read run ids on stdin, print their per-call usage as JSON")
    led.add_argument("database_url")
    cmp = sub.add_parser("compare", help="label=report[,report...] for each version, the baseline first")
    cmp.add_argument("versions", nargs="+")
    cmp.add_argument("--usage", help="JSON written by the ledger command")
    args = parser.parse_args(argv)

    if args.command == "runs":
        print("\n".join(sorted({row["run_id"] for row in _rows(args.reports) if row.get("run_id")})))
    elif args.command == "ledger":
        json.dump(ledger(args.database_url, sorted({line.strip() for line in sys.stdin if line.strip()})), sys.stdout)
    else:
        versions = {}
        for spec in args.versions:
            label, _, paths = spec.partition("=")
            if not paths:
                parser.error(f"expected label=report[,report...], got {spec!r}")
            versions[label] = _rows(paths.split(","))
        usage = json.loads(Path(args.usage).read_text()) if args.usage else None
        lines, totals = compare(versions, usage)
        print("\n".join(lines))
        print()
        base = next(iter(totals.values()))
        for label, total in totals.items():
            summary = ", ".join(f"{m} {total[m]:,.0f} ({_change(base[m], total[m])})" for m in METRICS if total[m])
            print(f"{label}: ok {total['ok']}/{total['n']}; {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
