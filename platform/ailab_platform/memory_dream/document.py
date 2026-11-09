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


def apply_ops(all_entries, shown, episodes, ops, user, scope):
    from html import unescape

    from .service import _user_part

    entries = copy.deepcopy(all_entries)
    by_id = {e["id"]: e for e in entries}
    allowed = {e["id"] for e in shown}
    evidence = {e.run_id: e for e in episodes}
    tombstones = tombstones_of(user)
    audit = []
    now = utcnow().isoformat()
    if not isinstance(ops, list) or len(ops) > 2000:
        raise ValueError("invalid document ops")
    touched = set()
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("op")
        ids = op.get("ids", []) if action == "merge" else [op.get("id")] if action in {"update", "delete"} else []
        if not isinstance(ids, list) or any(i not in allowed or i in touched for i in ids):
            continue
        targets = [by_id[i] for i in ids]
        if action in {"merge", "delete"} and any(protected(e) for e in targets):
            continue
        if action == "merge" and len(ids) < 2:
            continue
        if action == "delete" and op.get("reason") not in {"duplicate", "outdated"}:
            continue
        episode = evidence.get(op.get("run"))
        quote = op.get("quote", "")
        if isinstance(quote, str):
            quote = unescape(quote)
        if action in {"add", "update"}:
            if not episode or not isinstance(quote, str) or not quote.strip() or quote not in _user_part(episode.digest):
                continue
            if action == "update" and any(
                protected(e) and stamp(e.get("updated_at") or e.get("observed_at")) >= stamp(episode.observed_at) for e in targets
            ):
                continue
        content = op.get("content", "")
        if action not in {"add", "update", "merge", "delete"}:
            continue
        if action != "delete":
            if not isinstance(content, str) or not content.strip() or len(content) > 200 or reject_reason(content) or reject_reason(quote):
                continue
            if blocked_by_tombstone(tombstones, content, [episode.run_id] if episode else []):
                continue
            section = kind(targets[0]) if targets else op.get("kind")
            if section not in {"fact", "preference", "project", "interest", "reference"}:
                continue
            if any(
                kind(e) == section and fact_hash(e["content"]) == fact_hash(content) and e.get("project_id") == scope and e["id"] not in ids
                for e in active(entries)
            ):
                continue
            fresh = {
                "id": new_id("mem"),
                "kind": section,
                "content": content.strip(),
                "project_id": scope,
                "origin": targets[0].get("origin", "inferred") if action == "update" else "inferred",
                "status": "review" if review_required(content) else "active",
                "observed_at": stamp(episode.observed_at).isoformat() if episode else now,
                "valid_from": now,
                "updated_at": now,
                "reinforced_at": now,
                "evidence_run_ids": [episode.run_id]
                if episode
                else sorted({rid for e in targets for rid in e.get("evidence_run_ids", [])})[:50],
                "source_quote": quote,
                "source": "nightly",
                "created_by": "correction" if action == "update" else "condense" if action == "merge" else "addition",
            }
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
