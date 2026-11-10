"""Conversation search index (ADR 0039).

One row per conversation holds the union of its title, prompt and answer terms. SQLite indexes it with FTS5 (external
content, triggers keep it in sync); PostgreSQL stores a trigger-maintained tsvector and indexes it with GIN. The index
is an implementation detail, so ``compare_metadata`` drift checks ignore the FTS tables and the GIN index.
"""

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

FTS = "conversation_search_fts"


def upgrade():
    op.create_table(
        "conversation_search",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("terms", sa.Text(), server_default="", nullable=False),
        sa.Column("terms_tsv", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_search_conversation"),
    )
    op.create_index("ix_conversation_search_user", "conversation_search", ["user_id"])
    op.create_table(
        "search_index_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_watermark_id", sa.String(40), server_default="", nullable=False),
        sa.Column("conversation_watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_watermark_id", sa.String(40), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"CREATE VIRTUAL TABLE {FTS} USING fts5(terms, content='conversation_search', content_rowid='id')")
        op.execute(
            f"CREATE TRIGGER conversation_search_ai AFTER INSERT ON conversation_search BEGIN "
            f"INSERT INTO {FTS}(rowid, terms) VALUES (new.id, new.terms); END"
        )
        op.execute(
            f"CREATE TRIGGER conversation_search_ad AFTER DELETE ON conversation_search BEGIN "
            f"INSERT INTO {FTS}({FTS}, rowid, terms) VALUES ('delete', old.id, old.terms); END"
        )
        op.execute(
            f"CREATE TRIGGER conversation_search_au AFTER UPDATE ON conversation_search BEGIN "
            f"INSERT INTO {FTS}({FTS}, rowid, terms) VALUES ('delete', old.id, old.terms); "
            f"INSERT INTO {FTS}(rowid, terms) VALUES (new.id, new.terms); END"
        )
    else:
        # A stored tsvector lets the planner choose the GIN index instead of parsing every row's terms.
        op.execute("ALTER TABLE conversation_search ALTER COLUMN terms_tsv TYPE tsvector USING NULL")
        op.execute(
            "CREATE OR REPLACE FUNCTION conversation_search_tsv_update() RETURNS trigger AS $$ "
            "BEGIN NEW.terms_tsv := to_tsvector('simple', NEW.terms); RETURN NEW; END $$ LANGUAGE plpgsql"
        )
        op.execute("DROP TRIGGER IF EXISTS conversation_search_tsv_biu ON conversation_search")
        op.execute(
            "CREATE TRIGGER conversation_search_tsv_biu BEFORE INSERT OR UPDATE OF terms ON conversation_search "
            "FOR EACH ROW EXECUTE FUNCTION conversation_search_tsv_update()"
        )
        op.execute("CREATE INDEX ix_conversation_search_terms ON conversation_search USING gin (terms_tsv)")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS conversation_search_au")
        op.execute("DROP TRIGGER IF EXISTS conversation_search_ad")
        op.execute("DROP TRIGGER IF EXISTS conversation_search_ai")
        op.execute(f"DROP TABLE IF EXISTS {FTS}")
    else:
        op.execute("DROP TRIGGER IF EXISTS conversation_search_tsv_biu ON conversation_search")
        op.execute("DROP FUNCTION IF EXISTS conversation_search_tsv_update()")
        op.execute("DROP INDEX IF EXISTS ix_conversation_search_terms")
    op.drop_index("ix_conversation_search_user", table_name="conversation_search")
    op.drop_table("search_index_state")
    op.drop_table("conversation_search")
