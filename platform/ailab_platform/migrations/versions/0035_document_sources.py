"""Exact message attachments and indexed, immutable document derivatives."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ocr_page_results",
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("generation", sa.String(80), nullable=False),
        sa.Column("text_key", sa.String(600), nullable=False),
        sa.Column("engine", sa.String(80), nullable=False),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
    )
    op.create_index("ix_files_user_sha", "files", ["user_id", "sha256", "status", "deleted_at", "purged_at"])
    op.create_table(
        "rendered_pages",
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(40), primary_key=True),
        sa.Column("key", sa.String(600), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
    )
    op.create_table(
        "applied_message_positions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_kind", sa.String(24), nullable=False),
        sa.Column("message_id", sa.String(40), nullable=False),
        sa.Column("application_seq", sa.Integer(), nullable=False),
        sa.Column("alias_run_prompt_id", sa.String(40)),
        sa.UniqueConstraint("message_kind", "message_id", name="uq_applied_message_identity"),
    )
    op.create_index("ix_applied_message_order", "applied_message_positions", ["run_id", "application_seq", "message_id"])
    op.create_table(
        "message_file_refs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("message_kind", sa.String(24), nullable=False),
        sa.Column("message_id", sa.String(40), nullable=False),
        sa.Column("file_id", sa.String(40), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reference_origin", sa.String(24), nullable=False),
        sa.Column("filename_snapshot", sa.String(255)),
        sa.Column("upload_id", sa.String(40)),
        sa.Column("invalidated_epoch", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_kind", "message_id", "file_id", "role", name="uq_message_file_ref"),
    )
    op.create_index("ix_message_refs_order", "message_file_refs", ["run_id", "message_kind", "message_id", "ordinal", "file_id"])
    op.create_index("ix_message_refs_file", "message_file_refs", ["file_id", "run_id"])
    op.create_table(
        "file_derivations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("manifest_key", sa.String(600), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("file_id", "kind", "fingerprint", name="uq_file_derivation"),
    )
    op.create_table(
        "document_blocks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("derivation_id", sa.String(40), sa.ForeignKey("file_derivations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit_number", sa.Integer(), nullable=False),
        sa.Column("anchor", sa.String(300), nullable=False),
        sa.Column("text_key", sa.String(600), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.UniqueConstraint("derivation_id", "ordinal", name="uq_document_block_ordinal"),
    )
    op.create_index("ix_document_blocks_unit", "document_blocks", ["derivation_id", "unit_number", "ordinal"])
    op.create_index("ix_document_blocks_anchor", "document_blocks", ["derivation_id", "anchor", "ordinal"])
    _backfill()


def downgrade():
    op.drop_table("ocr_page_results")
    op.drop_index("ix_files_user_sha", table_name="files")
    op.drop_table("rendered_pages")
    op.drop_table("document_blocks")
    op.drop_table("file_derivations")
    op.drop_table("message_file_refs")
    op.drop_table("applied_message_positions")


def _backfill():
    import uuid

    connection = op.get_bind()
    metadata = sa.MetaData()
    refs = sa.Table("message_file_refs", metadata, autoload_with=connection)
    positions = sa.Table("applied_message_positions", metadata, autoload_with=connection)
    runs = sa.Table("runs", metadata, autoload_with=connection)
    queues = sa.Table("queued_messages", metadata, autoload_with=connection)

    def identity(prefix):
        return prefix + "_" + uuid.uuid4().hex[:20]

    def add_refs(kind, message_id, file_ids, run_id, created_at):
        rows = [
            dict(
                id=identity("ref"),
                run_id=run_id,
                message_kind=kind,
                message_id=message_id,
                file_id=fid,
                role="input",
                ordinal=n,
                reference_origin="unknown_legacy",
                created_at=created_at,
            )
            for n, fid in enumerate(dict.fromkeys(file_ids or []), 1)
        ]
        if rows:
            connection.execute(refs.insert(), rows)

    after = ""
    while True:
        batch = connection.execute(
            sa.select(runs.c.id, runs.c.input_file_ids, runs.c.created_at).where(runs.c.id > after).order_by(runs.c.id).limit(256)
        ).all()
        if not batch:
            break
        for row in batch:
            add_refs("run_prompt", row.id, row.input_file_ids, row.id, row.created_at)
            connection.execute(
                positions.insert().values(
                    id=identity("pos"), run_id=row.id, message_kind="run_prompt", message_id=row.id, application_seq=0
                )
            )
        after = batch[-1].id
    after = ""
    while True:
        batch = connection.execute(sa.select(queues).where(queues.c.id > after).order_by(queues.c.id).limit(256)).mappings().all()
        if not batch:
            break
        for row in batch:
            add_refs("queued_message", row["id"], row["file_ids"], row["run_id"], row["created_at"])
            if row["state"] == "started" and row["run_id"]:
                connection.execute(
                    positions.update()
                    .where(positions.c.message_id == row["run_id"], positions.c.message_kind == "run_prompt")
                    .values(message_kind="queued_message", message_id=row["id"], alias_run_prompt_id=row["run_id"])
                )
            if row["state"] == "steered" and row["run_id"]:
                runtime = connection.scalar(sa.select(runs.c.runtime_meta).where(runs.c.id == row["run_id"])) or {}
                applied = runtime.get("steer_ids") or []
                if row["id"] in applied:
                    connection.execute(
                        positions.insert().values(
                            id=identity("pos"),
                            run_id=row["run_id"],
                            message_kind="queued_message",
                            message_id=row["id"],
                            application_seq=applied.index(row["id"]) + 1,
                        )
                    )
        after = batch[-1]["id"]
