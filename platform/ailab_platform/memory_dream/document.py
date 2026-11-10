"""Single-call document planning and deterministic evidence guards."""

import copy
import json

from ..config import settings
from ..memory_recall.document import active, kind, render, used_chars
from ..memory_recall.guard import reject_reason
from ..memory_recall.store import stamp
from ..memory_recall.tombstones import blocked_by_tombstone, fact_hash, tombstones_of
from ..models import new_id, utcnow
from .prompts import CLEANUP, DOCUMENT_SYSTEM


def messages(episodes, entries, *, cleanup=False):
    """Static rules, then this batch's conversations (or the one-time re-sort instruction), then the document last."""
    from .service import _episode_payload

    return [
        {"role": "system", "content": DOCUMENT_SYSTEM},
        {"role": "user", "content": CLEANUP if cleanup else _episode_payload(episodes)},
        {
            "role": "user",
            "content": json.dumps({"max_chars": settings.memory_doc_max_chars, "document": render(entries)}, ensure_ascii=False),
        },
    ]


# Reason codes the model attaches to every op and ignored run. They explain, they never authorize: an unknown
# code is counted as "?" in the batch report. Only delete reads its code, because I (wrong) needs the user's words
# and the one-time re-sort may also drop any line as one-off (O), a finished task (K) or outside knowledge (W).
CODES = {
    "add": set("ABCD"),
    "transition": {"E"},
    "update": {"F"},
    "merge": {"G"},
    "delete": {"H", "I"},
    "ignored": set("TOKWPN"),
}
CLEANUP_DELETES = {"H", "O", "K", "W"}
KINDS = {"fact", "preference", "project", "interest", "reference"}


def code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "?"


def apply_ops(all_entries, shown, episodes, ops, user, scope, codes=None, label=None, *, cleanup=False):
    """`label` is the project this batch's conversations belong to; a new line carries it as origin_project_id.

    `cleanup`: the one-time re-sort under a new standard. There is no conversation to quote, so any line may be rewritten
    (update/merge/transition) or dropped (H/O/K/W) without a quote, and a line mixing two kinds may be split: an add that
    names the line it comes from (`from`) and inherits its sources. Nothing else is added.
    """
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

    def line(content, section, created_by, episode, quote, runs, tag):
        return {
            "id": new_id("mem"),
            "kind": section,
            "content": content.strip(),
            "project_id": scope,
            "status": "active",
            "observed_at": stamp(episode.observed_at).isoformat() if episode else now,
            "valid_from": now,
            "updated_at": now,
            "reinforced_at": now,
            "evidence_run_ids": runs,
            "source_quote": quote,
            "source": "nightly",
            "created_by": created_by,
            "origin_project_id": tag,
        }

    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("op")
        if action not in CODES or action == "ignored":
            continue
        reason = code(op.get("r"), CLEANUP_DELETES if cleanup and action == "delete" else CODES[action])
        codes[reason] = codes.get(reason, 0) + 1
        ids = op.get("ids", []) if action == "merge" else [op.get("id")] if action in {"update", "delete", "transition"} else []
        if not isinstance(ids, list) or any(i not in allowed or i in touched for i in ids):
            continue
        targets = [by_id[i] for i in ids]
        if action == "merge" and len(ids) < 2:
            continue
        if action == "delete" and reason == "?":
            continue
        episode = evidence.get(op.get("run"))
        quote = op.get("quote", "")
        if isinstance(quote, str):
            quote = unescape(quote)
        quoted = bool(episode) and isinstance(quote, str) and bool(quote.strip()) and quote in _user_part(episode.digest)
        split = []
        if cleanup and action == "add":
            # a re-sort adds only by splitting a line that mixes two kinds; the new line keeps that line's sources
            split = op.get("from") if isinstance(op.get("from"), list) else []
            if not split or any(i not in allowed for i in split):
                continue
            episode, quote = None, ""
        elif cleanup and action in {"update", "transition"}:
            episode, quote = None, ""  # a re-sort rewrites a line from the document itself, never from a quote
        elif action in {"add", "update", "transition"} or (action == "delete" and reason == "I"):
            if not quoted:
                continue
        if action == "transition":
            target = targets[0]
            past_kind = op.get("past_kind") if op.get("past_kind") in KINDS else kind(target)
            past, current = op.get("past", ""), op.get("now") or ""
            if not acceptable(past, quote, episode) or duplicate(past_kind, past, ids):
                continue
            runs = list(dict.fromkeys([*target.get("evidence_run_ids", []), *([episode.run_id] if episode else [])]))[-50:]
            # the old line is corrected by its past tense, so open conversations get the existing correction notice
            tag = target.get("origin_project_id")
            was = line(past, past_kind, "transition", episode, quote, runs, tag)
            entries.append(was)
            fresh_ids = [was["id"]]
            present = isinstance(current, str) and current.strip() and acceptable(current, quote, episode)
            if present and not duplicate(kind(target), current, ids):
                # the present is a plain addition: no notice; a clash with an active line or a tombstone keeps only the past
                entries.append(line(current, kind(target), "addition", episode, quote, runs, tag))
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
            origins = targets or [by_id[i] for i in split]
            fresh = line(
                content,
                section,
                "correction" if action == "update" else "condense" if action == "merge" else "addition",
                episode,
                quote,
                # an update keeps the line's earlier sources; deleting one source must not orphan what others support
                list(dict.fromkeys([*(targets[0].get("evidence_run_ids", []) if action == "update" else []), episode.run_id]))[-50:]
                if episode
                else sorted({rid for e in origins for rid in e.get("evidence_run_ids", [])})[:50],
                # update keeps its line's project; merge (and a split) keeps one only when every line agrees
                label if not origins else tags.pop() if len(tags := {e.get("origin_project_id") for e in origins}) == 1 else None,
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
        active(scoped()),
        key=lambda e: (str(e.get("reinforced_at") or e.get("observed_at") or ""), len(e.get("evidence_run_ids", [])), e["id"]),
    )
    for entry in candidates:
        if used_chars(scoped()) <= settings.memory_doc_max_chars:
            break
        entry["invalid_at"] = now
        entry["superseded_reason"] = "evicted"
        audit.append({"op": "delete", "ids": [entry["id"]], "reason": "capacity"})
    return entries, audit
