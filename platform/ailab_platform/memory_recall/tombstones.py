"""What the user deleted, kept only as hashes and evidence ids so the text itself is really gone."""

from __future__ import annotations

import hashlib
import re


def fact_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"[\W_]+", " ", text.casefold()).strip().encode("utf-8")).hexdigest()


def tombstones_of(user) -> list[dict]:
    return list(((user.settings or {}).get("memory") or {}).get("tombstones") or [])


def blocked_by_tombstone(tombstones: list[dict], text: str, evidence_run_ids: list[str] | None = None) -> bool:
    """A deleted fact stays deleted unless the user restates it: same text, or nothing but the same evidence."""
    if any(t.get("hash") == fact_hash(text) for t in tombstones):
        return True
    evidence = set(evidence_run_ids or [])
    gone = {rid for t in tombstones for rid in t.get("run_ids") or []}
    return bool(evidence) and evidence <= gone
