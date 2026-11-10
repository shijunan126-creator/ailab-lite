"""Unprotect nightly lines the user never asked for (ADR 0036).

Before ADR 0035 the nightly extractor wrote every line as origin=explicit, meaning "backed by the user's words". ADR 0035
made explicit mean "the user asked for this line", which protects it from the nightly rewrite, without migrating those rows,
so one-off questions and delivered tasks became permanent. A line the user did ask for is kept protected:

- the conversation tool (explicit=true) and the memory page write source agent/user, or origin edited, never source nightly;
- a sensitive nightly line was only ever active after the user confirmed it (it was created for review);
- a nightly quote that asks to remember ("记住" ...) is the user's request even though the nightly job wrote it;
- a line that absorbed a user-protected line in a project merge inherited that protection.

The patterns are frozen here so later policy changes do not change what this revision did. The demoted rows carry
metadata.origin_before so the downgrade restores exactly them.
"""

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

SENSITIVE = re.compile(r"诊断|患有|病史|宗教信仰|政治倾向|性取向|身份证|银行卡|diagnosed|medical condition|sexual orientation", re.I)
ASKED = re.compile(r"记住|记下|记一下|别忘|不要忘|remember", re.I)
USER_SOURCES = ("agent", "user")


def _meta(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _write(db, row_id, origin, metadata):
    db.execute(
        sa.text("UPDATE memory_items SET origin = :origin, metadata = :metadata WHERE id = :id").bindparams(
            sa.bindparam("metadata", type_=sa.JSON)
        ),
        {"origin": origin, "metadata": metadata, "id": row_id},
    )


def upgrade():
    db = op.get_bind()
    candidates = db.execute(
        sa.text("SELECT id, content, source_quote, metadata FROM memory_items WHERE origin = 'explicit' AND status IN ('active', 'review')")
    ).mappings()
    candidates = [r for r in candidates if _meta(r["metadata"]).get("source") == "nightly"]
    if not candidates:
        return
    # targets of a project merge that took in a line the user had protected
    absorbed = set()
    merged = db.execute(
        sa.text(
            "SELECT superseded_by, origin, metadata FROM memory_items WHERE superseded_reason = 'condense' AND superseded_by IS NOT NULL"
        )
    ).mappings()
    for row in merged:
        if row["origin"] in ("edited", "manual") or (row["origin"] == "explicit" and _meta(row["metadata"]).get("source") in USER_SOURCES):
            absorbed.add(row["superseded_by"])
    for row in candidates:
        if SENSITIVE.search(row["content"] or "") or ASKED.search(row["source_quote"] or "") or row["id"] in absorbed:
            continue
        _write(db, row["id"], "inferred", {**_meta(row["metadata"]), "origin_before": "explicit"})


def downgrade():
    db = op.get_bind()
    for row in db.execute(sa.text("SELECT id, origin, metadata FROM memory_items WHERE origin = 'inferred'")).mappings():
        metadata = _meta(row["metadata"])
        if metadata.pop("origin_before", None) == "explicit":
            _write(db, row["id"], "explicit", metadata)
