"""Aggregate checker output; semantic failures come from an explicit human review file.

Numeric literal flags are review aids, not a claim to verify arbitrary formulas or JS.
"""

import argparse
import json
import re
from pathlib import Path
from statistics import mean

from cases import CASES

CONTROL_COMPONENTS = {"Select", "Slider", "Toggle", "Input", "Tabs"}


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def score(answers, checks, judgments):
    cases = {c["id"]: c for c in CASES}
    rows = {(r["case"], r["turn"]): r for r in checks}
    blocks = [b for r in checks for b in r["blocks"]]
    closed = [b for b in blocks if b["closed"]]
    no_ui = [r for r in checks if r["kind"] == "no_ui"]
    controls = [r for r in checks if r["kind"] == "controls"]
    html = [r for r in checks if r["kind"] == "html"]
    failed = set()
    details = []
    for answer in answers:
        key = (answer["case"], answer["turn"])
        case = cases[key[0]]
        checked = rows[key]
        review = judgments.get(f"{key[0]}:{key[1]}", {})
        wrong = review.get("wrong_statements")
        if wrong is not None and (not isinstance(wrong, list) or any(not isinstance(q, str) or not q.strip() for q in wrong)):
            raise ValueError(f"Invalid wrong_statements for {key}")
        if "restated_outside" in review and not isinstance(review["restated_outside"], bool):
            raise ValueError(f"Invalid restated_outside for {key}")
        valid = bool(checked["blocks"]) and all(
            b["closed"] and b["valid"] and b.get("displaysCorrectly", True) and b.get("safe", True) for b in checked["blocks"]
        )
        edit_ok = valid and review.get("edit_success", False) if key[1] == 2 else None
        malformed = any(b.get("displaysCorrectly") is False for b in checked["blocks"])
        if answer.get("error") or malformed or any(not b["valid"] or b.get("safe") is False for b in checked["blocks"]):
            failed.add(key[0])
        if case["kind"] == "html" and not any("HtmlArtifact" in b["components"] for b in checked["blocks"]):
            failed.add(key[0])
        if case["kind"] == "controls" and not any(set(b["components"]) & CONTROL_COMPONENTS for b in checked["blocks"]):
            failed.add(key[0])
        if case["kind"] == "no_ui" and checked["blocks"]:
            failed.add(key[0])
        if key[1] == 2 and not edit_ok:
            failed.add(key[0])
        components = {name for b in checked["blocks"] for name in b["components"]}
        if case["kind"] == "no_ui":
            strict = not answer.get("error") and not checked["blocks"]
        elif case["kind"] == "html":
            strict = valid and "HtmlArtifact" in components and review.get("html_success") is not False and not answer.get("error")
        else:
            # Missing semantic review is unknown, never a successful data sample.
            structural = valid and not answer.get("error") and review.get("html_success") is not False
            if case["kind"] == "controls":
                structural = structural and bool(components & CONTROL_COMPONENTS)
            if key[1] == 2:
                structural = structural and edit_ok and case["expected"] in components
            strict = False if not structural else (not wrong if wrong is not None else None)
        if strict is False:
            failed.add(key[0])
        # This intentionally includes display ordinals, CSS constants and valid computed results.
        # A human must decide provenance and correctness; never count these flags as errors.
        given = set(re.findall(r"\d+(?:\.\d+)?", case["prompt"] + (case["edit"] or "")))
        literals = set(re.findall(r"(?<![\w])\d+(?:\.\d+)?", answer["answer"]))
        details.append(
            dict(
                case=key[0],
                turn=key[1],
                valid=valid,
                edit_success=edit_ok,
                strict_consistent=strict,
                wrong_statements=review.get("wrong_statements"),
                restated_outside=review.get("restated_outside"),
                review=review,
                latency_seconds=answer["latency_seconds"],
                usage=answer.get("usage", {}),
                numeric_review_flags=sorted(literals - given),
            )
        )
    # Keep the original 23-item final-state denominator; edit baselines are reviewed separately.
    reviewed = [
        d
        for d in details
        if cases[d["case"]]["kind"] not in {"no_ui", "html"} and not (cases[d["case"]]["kind"] == "edit" and d["turn"] == 1)
    ]
    usage = {}
    for a in answers:
        for name, value in a.get("usage", {}).items():
            if isinstance(value, (int, float)):
                usage[name] = usage.get(name, 0) + value
    return dict(
        cases=len({a["case"] for a in answers}),
        responses=len(answers),
        blocks=len(blocks),
        closed_blocks=len(closed),
        valid_closed_blocks=sum(b["valid"] for b in closed),
        closed_block_validity=sum(b["valid"] for b in closed) / len(closed) if closed else None,
        unclosed_blocks=len(blocks) - len(closed),
        malformed_evaluated_blocks=sum(b.get("displaysCorrectly") is False for b in blocks),
        raw_html_fences=sum(len(re.findall(r"^\s*```html\b", a["answer"], re.MULTILINE)) for a in answers),
        transport_errors=sum(bool(a.get("error")) for a in answers),
        no_ui_cases=len(no_ui),
        unwanted_ui=sum(bool(r["blocks"]) for r in no_ui),
        edit_cases=len({r["case"] for r in checks if r["kind"] == "edit"}),
        edit_success=sum(d["edit_success"] is True for d in details),
        data_reviewed=len(reviewed),
        data_consistent=sum(d["strict_consistent"] is True for d in reviewed),
        strict_consistent=sum(d["strict_consistent"] is True for d in reviewed),
        semantic_unreviewed=sum(d["wrong_statements"] is None for d in reviewed),
        restated_outside=sum(d["restated_outside"] is True for d in details),
        answers_with_wrong_statements=sum(bool(d["wrong_statements"]) for d in details),
        wrong_statements=sum(len(d["wrong_statements"] or []) for d in details),
        conclusion_compliant=sum(d["review"].get("conclusion_compliant", False) for d in reviewed),
        numeric_correct=sum(d["review"].get("numeric_correct", False) for d in reviewed),
        expression_provenance=sum(d["review"].get("expression_provenance", False) for d in reviewed),
        controls_cases=len(controls),
        controls_used=sum(any(set(b["components"]) & CONTROL_COMPONENTS for b in r["blocks"]) for r in controls),
        html_cases=len(html),
        html_used=sum(any("HtmlArtifact" in b["components"] for b in r["blocks"]) for r in html),
        html_elsewhere=[
            f"{r['case']}:{r['turn']}"
            for r in checks
            if r["kind"] != "html" and any("HtmlArtifact" in b["components"] for b in r["blocks"])
        ],
        average_block_chars=mean(b["chars"] for b in blocks) if blocks else 0,
        average_latency_seconds=mean(a["latency_seconds"] for a in answers),
        usage=usage,
        failing_cases=sorted(failed),
        details=details,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("answers")
    parser.add_argument("checks")
    parser.add_argument("--judgments", default=str(Path(__file__).with_name("judgments.json")))
    args = parser.parse_args()
    judgments = json.loads(Path(args.judgments).read_text()) if Path(args.judgments).exists() else {}
    print(json.dumps(score(read(args.answers), read(args.checks), judgments), ensure_ascii=False, indent=2))
