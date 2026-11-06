"""A finished consumer must not strand a physically returned file executor."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from ailab_platform.api import file_work
from ailab_platform.models import Base, DocumentOperation, FileObject, OutboxMessage, User


@pytest.mark.parametrize("fence", [None, "attempt", "version", "deadline", "disabled"])
def test_preview_completion_releases_lane_without_live_consumer_but_keeps_writer_fences(fence):
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def functions(connection, _):
        connection.create_function("ailab_search_lower", 1, lambda value: value.lower() if value else "", deterministic=True)

    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            now = datetime.now(UTC)
            db.add(User(id="u", email="workflow@test.local", display_name="workflow"))
            db.flush()
            file = FileObject(
                id="f",
                user_id="u",
                filename="a.pdf",
                sha256="v",
                storage_key="uploads/f",
                status="ready",
                meta={"scan": {"verdict": "clean"}, "preview_job_id": "j"},
            )
            job = OutboxMessage(
                id="j",
                topic="file",
                payload={"kind": "preview", "file_id": "f"},
                claim_token="attempt",
                claimed_until=now + timedelta(seconds=120),
            )
            row = DocumentOperation(
                id="j",
                user_id="u",
                file_id="f",
                job_id="j",
                source_version="v",
                identity="identity",
                lane="render",
                kind="file_job",
                binding={"job_kind": "preview", "preparations": []},
                status="running",
                deadline=now + timedelta(seconds=600),
                execution_deadline=now + timedelta(seconds=120),
                lease_until=now + timedelta(seconds=125),
                claim_token="claim",
                attempt_token="attempt",
                executor_started_at=now,
            )
            db.add_all([file, job, row])
            db.commit()
            assert not file_work.available(db, row)  # last-page consumer is gone
            if fence == "version":
                file.sha256 = "new"
            elif fence == "deadline":
                row.execution_deadline = now - timedelta(seconds=1)
            elif fence == "disabled":
                db.get(User, "u").disabled_at = now
            db.commit()
            if fence:
                with pytest.raises(HTTPException) as error:
                    file_work.finish(db, "j", "stale" if fence == "attempt" else "attempt")
                assert error.value.status_code == 409
                assert row.status == "running"
            else:
                file_work.finish(db, "j", "attempt")
                db.commit()
                assert row.status == "ready" and row.claim_token is None and row.lease_until is None
    finally:
        engine.dispose()
