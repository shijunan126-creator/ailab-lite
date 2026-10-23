# Adapter contract groups

Run one group at a time on small machines (never use developer databases):

```bash
pytest tests/contracts/test_storage.py -ra   # local; S3 skips without all three env vars
pytest tests/contracts/test_queue.py -ra     # memory; Redis skips without URL
pytest tests/contracts/test_database.py -ra  # SQLite; PG uses a private temporary schema
pytest tests/contracts/test_backup.py tests/contracts/test_lifecycle.py -ra
```

Real endpoints: `AILAB_TEST_REDIS_URL=redis://127.0.0.1:16450/15`, `AILAB_TEST_POSTGRES_URL=postgresql+psycopg://USER@127.0.0.1:15450/ailab_migrations`, `AILAB_TEST_S3_URL=http://127.0.0.1:19450`, `AILAB_TEST_S3_ACCESS_KEY`, `AILAB_TEST_S3_SECRET_KEY`. Only throwaway services: the rest of the suite wipes the PG migration DB. S3 creates a unique temporary bucket per test and removes it. The PostgreSQL backup drill additionally requires CREATE/DROP DATABASE privileges on the throwaway server. CI starts MinIO in a dedicated service-container job after Python tests; it is a protocol fixture, not a production storage recommendation. Its server command is explicit because the upstream image defaults to `minio` ([upstream Dockerfile](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/Dockerfile)).

`test_gc_queries_have_indexed_plans` proves index eligibility on both SQL dialects (PG disables sequential scans for the empty fixture); it is not a production-scale latency benchmark. Backup's SQLite drill starts a separate application process, verifies file bytes and reads a conversation after restoring to an empty directory. The GC interleaving holds the real user lock while a completion freezes its object and a collector waits.

Performance probe: `python scripts/measure_storage.py --mib 50` then `--mib 100`. On this macOS checkout, 50/100 MiB stream write + copy + read took 0.15/0.38 s, Python allocation peak 148,697 bytes, RSS growth 278,528/688,128 bytes; two objects used 100/200 MiB of disk plus tiny metadata. These are local adapter measurements, not target-node or Broker/Worker acceptance.
