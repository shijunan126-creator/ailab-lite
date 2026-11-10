"""One kind of memory line (ADR 0037).

Lines no longer carry who wrote them (explicit/inferred/chat/edited/manual): every line is "memory", any of them may
be condensed by the nightly job, and the user corrects them on the page or in the edit box. The review/suppressed
states are gone too: a line waiting for confirmation becomes active, unless it holds an id, card number or credential,
which is never remembered; such a line is closed as deleted. A review line that duplicates an active one is closed as
condensed instead, so the partial unique index on active lines holds.

The patterns are frozen here so later guard changes do not change what this revision did. Changed rows carry
metadata.origin_0069 / status_0069 so the downgrade restores exactly them (0068 keeps its own origin_before).
"""

import json
import re
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

NEVER = [
    re.compile(p, re.I)
    for p in (
        r"(?<![0-9])[0-9]{17}[0-9x](?![0-9a-z])",
        r"(?<![0-9])[0-9](?:[ \-]?[0-9]){15,18}(?![0-9])",
        r"(?:password|passwd|pwd|secret|api[_\- ]?key|access[_\- ]?token|token)\s*(?:is|are|[:=：])\s*\S{6,}",
        r"(?:密码|口令|密钥|令牌)\s*(?:是|为|[:=：])\s*\S{4,}",
    )
]


def _meta(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _update(db, row_id, values, metadata):
    sets = ", ".join(f"{key} = :{key}" for key in values)
    db.execute(
        sa.text(f"UPDATE memory_items SET {sets}, metadata = :metadata WHERE id = :id").bindparams(sa.bindparam("metadata", type_=sa.JSON)),
        {**values, "metadata": metadata, "id": row_id},
    )


def upgrade():
    db = op.get_bind()
    now = datetime.now(UTC)
    rows = list(
        db.execute(
            sa.text(
                "SELECT id, user_id, project_id, kind, content_hash, content, origin, status, metadata FROM memory_items "
                "WHERE status IN ('active', 'review', 'suppressed')"
            )
        ).mappings()
    )
    active = {(r["user_id"], r["project_id"] or "", r["kind"], r["content_hash"]) for r in rows if r["status"] == "active"}
    for row in rows:
        metadata = _meta(row["metadata"])
        values = {}
        if row["origin"] != "memory":
            metadata["origin_0069"] = row["origin"]
            values["origin"] = "memory"
        if row["status"] != "active":
            metadata["status_0069"] = row["status"]
            key = (row["user_id"], row["project_id"] or "", row["kind"], row["content_hash"])
            if any(p.search(row["content"] or "") for p in NEVER):
                values.update(status="closed", valid_to=now, superseded_reason="deleted")
            elif key in active:
                values.update(status="closed", valid_to=now, superseded_reason="condense")
            else:
                values["status"] = "active"
                active.add(key)
        if values:
            _update(db, row["id"], values, metadata)


def downgrade():
    db = op.get_bind()
    for row in db.execute(sa.text("SELECT id, metadata FROM memory_items")).mappings():
        metadata = _meta(row["metadata"])
        if "origin_0069" not in metadata and "status_0069" not in metadata:
            continue
        values = {}
        if "origin_0069" in metadata:
            values["origin"] = metadata.pop("origin_0069")
        if "status_0069" in metadata:
            values.update(status=metadata.pop("status_0069"), valid_to=None, superseded_reason=None)
        _update(db, row["id"], values, metadata)
