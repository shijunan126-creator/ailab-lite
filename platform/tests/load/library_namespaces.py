"""Folder/name lookup plans on 100k siblings; private DB/schema only."""

import argparse
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

os.environ["AILAB_ENV_FILE"] = "/tmp/ailab-benchmark-no.env"
os.environ["AILAB_DATA_DIR"] = tempfile.mkdtemp(prefix="ailab-names-config-")

from ailab_platform.api.routes_files import list_file_folders
from ailab_platform.models import Base, FileObject, Project, User
from ailab_platform.naming import name_key


def probe(engine):
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(User.__table__.insert(), {"id": "bench", "email": "bench@example.invalid", "display_name": "Bench"})
        c.execute(Project.__table__.insert(), [{"id": f"p{i}", "user_id": "bench", "name": f"Folder {i}"} for i in range(10)])
        for start in range(0, 100000, 1000):
            c.execute(
                FileObject.__table__.insert(),
                [
                    {
                        "id": f"f{i:06}",
                        "lineage_id": f"f{i:06}",
                        "user_id": "bench",
                        "filename": f"file-{i}.txt",
                        "content_type": "image/png" if i % 17 == 0 else "text/plain",
                        "storage_key": f"objects/{i}",
                        "project_id": None if i % 10 == 0 else f"p{i % 10}",
                        "purpose": "skill" if i % 19 == 0 else "library",
                        "status": "ready",
                    }
                    for i in range(start, start + 1000)
                ],
            )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as stats:
        stats.execute(text("VACUUM ANALYZE files" if engine.dialect.name == "postgresql" else "ANALYZE"))
    with engine.connect() as c:
        base = "user_id='bench' AND purpose='library' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL"
        queries = {
            "root": f"SELECT id FROM files WHERE {base} AND project_id IS NULL ORDER BY created_at DESC,id DESC LIMIT 100",
            "folder": f"SELECT id FROM files WHERE {base} AND project_id='p1' ORDER BY created_at DESC,id DESC LIMIT 100",
            "name": f"SELECT id FROM files WHERE {base} AND coalesce(project_id,'')='p1' AND name_key='{name_key('file-111.txt')}' LIMIT 1",
            "groups": f"SELECT project_id,count(*) FROM files WHERE {base} AND project_id>'' "
            "GROUP BY project_id ORDER BY project_id LIMIT 100",
        }
        result = {}
        for key, q in queries.items():
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = [list(r) for r in c.execute(text(prefix + q))]
            if key != "groups":
                assert any("INDEX" in str(r).upper() for r in plan), plan
            if key == "name":
                assert "uq_files_folder_name" in str(plan), plan
            samples = []
            for _ in range(10):
                t = time.perf_counter()
                c.execute(text(q)).all()
                samples.append((time.perf_counter() - t) * 1000)
            result[key] = {"plan": plan, "p95_ms": round(sorted(samples)[-1], 2)}
        for trashed, category, q in [(False, "all", ""), (False, "images", "file"), (True, "all", "")]:
            captured = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                captured.append((statement, parameters))

            event.listen(engine, "before_cursor_execute", capture)
            samples = []
            try:
                with Session(engine) as db:
                    for _ in range(10):
                        started = time.perf_counter()
                        list_file_folders(category=category, trashed=trashed, q=q, limit=100, user=User(id="bench"), db=db)
                        samples.append((time.perf_counter() - started) * 1000)
            finally:
                event.remove(engine, "before_cursor_execute", capture)
            statement, parameters = captured[-1]
            plan = [list(r) for r in c.exec_driver_sql(prefix + statement, parameters)]
            result[f"api_{category}_{'trash' if trashed else 'live'}"] = {
                "plan": plan,
                "p95_ms": round(sorted(samples)[-1], 2),
            }
        return {"files": 100000, "dialect": engine.dialect.name, "queries": result}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--postgres-url")
    a = p.parse_args()
    if a.postgres_url:
        admin = create_engine(a.postgres_url)
        schema = "names_" + uuid.uuid4().hex
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(a.postgres_url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            print(json.dumps(probe(engine), indent=2))
        finally:
            engine.dispose()
            with admin.begin() as c:
                c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
    else:
        with tempfile.TemporaryDirectory() as d:
            engine = create_engine(f"sqlite:///{Path(d) / 'test.db'}")
            print(json.dumps(probe(engine), indent=2))
            engine.dispose()
