"""Single-call document planning and deterministic evidence/protection guards."""

import copy
import json

from ..config import settings
from ..memory_recall.document import active, kind, render, used_chars
from ..memory_recall.guard import reject_reason
from ..memory_recall.policy import review_required
from ..memory_recall.store import stamp
from ..memory_recall.tombstones import blocked_by_tombstone, fact_hash, tombstones_of
from ..models import new_id, utcnow
from .prompts import DOCUMENT_SYSTEM


def messages(episodes, entries):
    from .service import _episode_payload

    return [
        {"role": "system", "content": DOCUMENT_SYSTEM},
        {"role": "user", "content": _episode_payload(episodes)},
        {
            "role": "user",
            "content": json.dumps({"max_chars": settings.memory_doc_max_chars, "document": render(entries)}, ensure_ascii=False),
        },
    ]


def protected(entry):
    return entry.get("origin") in {"explicit", "edited", "manual"}


# Reason codes the model attaches to every op and ignored run. They explain, they never authorize: an unknown
# code is counted as "?" in the batch report. Only delete reads its code, because I (wrong) needs the user's words.
CODES = {
    "add": set("ABCD"),
    "transition": {"E"},
    "update": {"F"},
    "merge": {"G"},
    "delete": {"H", "I"},
    "ignored": set("TOKWPN"),
}
KINDS = {"fact", "preference", "project", "interest", "reference"}


def code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "?"


def apply_ops(all_entries, shown, episodes, ops, user, scope, codes=None):
    from html import unescape

    from .service import _user_part

    entries = copy.deepcopy(all_entries)
    by_id = {e["id"]: e for e in entries}
    allowed = {e["id"] for e in shown}
    evidence = {e.run_id: e for e in episodes}
    tombstones = tombstones_of(user)
    audit = []
    codes = {} if codes is None else codes
    now = utcnow().isoformat()
    if not isinstance(ops, list) or len(ops) > 2000:
        raise ValueError("invalid document ops")
    touched = set()

    def duplicate(section, content, ids):
        return any(
            kind(e) == section and fact_hash(e["content"]) == fact_hash(content) and e.get("project_id") == scope and e["id"] not in ids
            for e in active(entries)
        )

    def acceptable(content, quote, episode):
        return (
            isinstance(content, str)
            and content.strip()
            and len(content) <= 200
            and not reject_reason(content)
            and not reject_reason(quote)
            and not blocked_by_tombstone(tombstones, content, [episode.run_id] if episode else [])
        )

    def line(content, section, origin, created_by, episode, quote, runs):
        return {
            "id": new_id("mem"),
            "kind": section,
            "content": content.strip(),
            "project_id": scope,
            "origin": origin,
            "status": "review" if review_required(content) else "active",
            "observed_at": stamp(episode.observed_at).isoformat() if episode else now,
            "valid_from": now,
            "updated_at": now,
            "reinforced_at": now,
            "evidence_run_ids": runs,
            "source_quote": quote,
            "source": "nightly",
            "created_by": created_by,
        }

    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("op")
        if action not in CODES or action == "ignored":
            continue
        reason = code(op.get("r"), CODES[action])
        codes[reason] = codes.get(reason, 0) + 1
        ids = op.get("ids", []) if action == "merge" else [op.get("id")] if action in {"update", "delete", "transition"} else []
        if not isinstance(ids, list) or any(i not in allowed or i in touched for i in ids):
            continue
        targets = [by_id[i] for i in ids]
        if action in {"merge", "delete"} and any(protected(e) for e in targets):
            continue
        if action == "merge" and len(ids) < 2:
            continue
        if action == "delete" and reason == "?":
            continue
        episode = evidence.get(op.get("run"))
        quote = op.get("quote", "")
        if isinstance(quote, str):
            quote = unescape(quote)
        quoted = bool(episode) and isinstance(quote, str) and bool(quote.strip()) and quote in _user_part(episode.digest)
        if action in {"add", "update", "transition"} or (action == "delete" and reason == "I"):
            if not quoted:
                continue
            if action in {"update", "transition"} and any(
                protected(e) and stamp(e.get("updated_at") or e.get("observed_at")) >= stamp(episode.observed_at) for e in targets
            ):
                continue
        if action == "transition":
            target = targets[0]
            past_kind = op.get("past_kind") if op.get("past_kind") in KINDS else kind(target)
            past, current = op.get("past", ""), op.get("now") or ""
            if not acceptable(past, quote, episode) or duplicate(past_kind, past, ids):
                continue
            runs = list(dict.fromkeys([*target.get("evidence_run_ids", []), episode.run_id]))[-50:]
            # the old line is corrected by its past tense, so open conversations get the existing correction notice
            was = line(past, past_kind, target.get("origin", "inferred"), "transition", episode, quote, runs)
            entries.append(was)
            fresh_ids = [was["id"]]
            present = isinstance(current, str) and current.strip() and acceptable(current, quote, episode)
            if present and not duplicate(kind(target), current, ids):
                # the present is a plain addition: no notice; a clash with an active line or a tombstone keeps only the past
                entries.append(line(current, kind(target), "inferred", "addition", episode, quote, runs))
                fresh_ids.append(entries[-1]["id"])
            target.update(superseded_reason="correction", invalid_at=now, updated_at=now, superseded_by=was["id"])
            touched.update([*ids, *fresh_ids])
            audit.append({"op": action, "ids": ids, "new_id": was["id"], "now_id": fresh_ids[1] if len(fresh_ids) > 1 else None})
            continue
        content = op.get("content", "")
        if action != "delete":
            if not acceptable(content, quote, episode):
                continue
            section = kind(targets[0]) if targets else op.get("kind")
            if section not in KINDS or duplicate(section, content, ids):
                continue
            fresh = line(
                content,
                section,
                targets[0].get("origin", "inferred") if action == "update" else "inferred",
                "correction" if action == "update" else "condense" if action == "merge" else "addition",
                episode,
                quote,
                # an update keeps the line's earlier sources; deleting one source must not orphan what others support
                list(dict.fromkeys([*(targets[0].get("evidence_run_ids", []) if action == "update" else []), episode.run_id]))[-50:]
                if episode
                else sorted({rid for e in targets for rid in e.get("evidence_run_ids", [])})[:50],
            )
            entries.append(fresh)
        for target in targets:
            target["superseded_reason"] = "correction" if action == "update" else "condense" if action == "merge" else "deleted"
            target["invalid_at"] = now
            target["updated_at"] = now
            if action != "delete":
                target["superseded_by"] = fresh["id"]
        touched.update(ids)
        audit.append({"op": action, "ids": ids, "new_id": fresh["id"] if action != "delete" else None})

    def scoped():
        return [e for e in entries if e.get("project_id") == scope]

    candidates = sorted(
        (e for e in active(scoped()) if e.get("origin") == "inferred"),
        key=lambda e: (str(e.get("reinforced_at") or e.get("observed_at") or ""), len(e.get("evidence_run_ids", [])), e["id"]),
    )
    for entry in candidates:
        if used_chars(scoped()) <= settings.memory_doc_max_chars:
            break
        entry["invalid_at"] = now
        entry["superseded_reason"] = "evicted"
        audit.append({"op": "delete", "ids": [entry["id"]], "reason": "capacity"})
    return entries, audit
