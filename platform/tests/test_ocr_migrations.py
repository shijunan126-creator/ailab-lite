"""Both database engines retain file and Run billing across upgrade/downgrade."""

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from ailab_platform.migrate import alembic_config, run_migrations
from ailab_platform.models import ModelUsage, UsageReservation
from tests.test_migrations import _downgrade_to, _seed_user_and_run
from tests.test_migrations import engine as engine


def test_file_ledger_roundtrip_preserves_owner_amount_and_subject(engine):
    run_migrations(engine)
    with engine.begin() as conn:
        _seed_user_and_run(conn)
    with Session(engine) as db:
        from datetime import UTC, datetime
        from decimal import Decimal

        db.add(
            ModelUsage(call_id="ocr-test", run_id=None, subject="file_job", file_id="f1", user_id="u1", mode="ocr", weighted_amount=123000)
        )
        db.add(ModelUsage(call_id="run-test", run_id="r1", user_id="u1", mode="work", weighted_amount=45000))
        db.add(
            UsageReservation(
                call_id="ocr-open",
                run_id=None,
                subject="file_job",
                file_id="f1",
                user_id="u1",
                mode="ocr",
                attempt_id="a",
                alias="m",
                max_output_tokens=100,
                estimated_input_tokens=100,
                weight=Decimal(1),
                cached_token_weight=Decimal("0.1"),
                policy_version="v1",
                reserved_amount=200000,
                expires_at=datetime.now(UTC),
            )
        )
        db.commit()
    parent = ScriptDirectory.from_config(alembic_config()).get_revision("0018").down_revision
    _downgrade_to(engine, parent)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT run_id, weighted_amount FROM model_usage WHERE call_id='ocr-test'")).one() == ("f1", 123000)
    run_migrations(engine)  # ORM describes head, not the intermediate 0018 schema.
    with Session(engine) as db:
        row = db.query(ModelUsage).filter_by(call_id="ocr-test").one()
        assert (row.run_id, row.file_id, row.subject, row.user_id, row.weighted_amount) == (None, "f1", "file_job", "u1", 123000)
        assert db.get(UsageReservation, "ocr-open").subject == "file_job"
        run = db.query(ModelUsage).filter_by(call_id="run-test").one()
        assert (run.run_id, run.subject, run.weighted_amount) == ("r1", "run", 45000)
