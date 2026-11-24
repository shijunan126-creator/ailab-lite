"""File mention name index (ADR 0046). Existing heads are backfilled by the API."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

ELIGIBLE = "purpose = 'library' AND status = 'ready' AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"


def upgrade():
    for name, columns in (
        ("ix_files_mentions_recent", ["purpose", "status", "user_id", "created_at", "id"]),
        ("ix_files_mentions_project_recent", ["purpose", "status", "user_id", "project_id", "created_at", "id"]),
    ):
        op.create_index(name, "files", columns, sqlite_where=sa.text(ELIGIBLE), postgresql_where=sa.text(ELIGIBLE))
    op.create_table(
        "file_search",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lineage_id", sa.String(40), nullable=False),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("terms_tsv", TSVECTOR().with_variant(sa.Text(), "sqlite")),
        sa.UniqueConstraint("lineage_id", name="uq_file_search_lineage"),
        sa.UniqueConstraint("file_id", name="uq_file_search_file"),
    )
    op.create_index("ix_file_search_recent", "file_search", ["user_id", sa.text("created_at DESC"), "file_id"])
    op.create_table(
        "file_search_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("watermark_user_id", sa.String(40), nullable=False),
        sa.Column("watermark_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watermark_file_id", sa.String(40), nullable=False),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute("CREATE VIRTUAL TABLE file_search_fts USING fts5(terms, content='file_search', content_rowid='id', prefix='3 5 7')")
        op.execute(
            "CREATE TRIGGER file_search_ai AFTER INSERT ON file_search BEGIN "
            "INSERT INTO file_search_fts(rowid, terms) VALUES (new.id, new.terms); END"
        )
        op.execute(
            "CREATE TRIGGER file_search_ad AFTER DELETE ON file_search BEGIN "
            "INSERT INTO file_search_fts(file_search_fts, rowid, terms) VALUES ('delete', old.id, old.terms); END"
        )
        op.execute(
            "CREATE TRIGGER file_search_au AFTER UPDATE OF terms ON file_search WHEN old.terms != new.terms BEGIN "
            "INSERT INTO file_search_fts(file_search_fts, rowid, terms) VALUES ('delete', old.id, old.terms); "
            "INSERT INTO file_search_fts(rowid, terms) VALUES (new.id, new.terms); END"
        )
    else:
        op.execute(
            "CREATE TRIGGER file_search_tsv_biu BEFORE INSERT OR UPDATE OF terms ON file_search "
            "FOR EACH ROW EXECUTE FUNCTION tsvector_update_trigger(terms_tsv, 'pg_catalog.simple', terms)"
        )
        op.execute("CREATE INDEX ix_file_search_terms ON file_search USING gin (terms_tsv)")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER file_search_au")
        op.execute("DROP TRIGGER file_search_ad")
        op.execute("DROP TRIGGER file_search_ai")
        op.execute("DROP TABLE file_search_fts")
    op.drop_table("file_search_state")
    op.drop_table("file_search")
    op.drop_index("ix_files_mentions_project_recent", table_name="files")
    op.drop_index("ix_files_mentions_recent", table_name="files")
