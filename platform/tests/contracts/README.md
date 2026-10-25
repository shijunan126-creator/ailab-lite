# Adapter contract groups

Run one group at a time on small machines (never use developer databases):

```bash
pytest tests/contracts/test_storage.py -ra   # local; S3 skips without all three env vars
pytest tests/contracts/test_queue.py -ra     # memory; Redis skips without URL
pytest tests/contracts/test_database.py -ra  # SQLite; PG uses a private temporary schema
pytest tests/contracts/test_backup.py tests/contracts/test_lifecycle.py -ra
```

Real endpoints: `AILAB_TEST_REDIS_URL=redis://127.0.0.1:16450/15`, `AILAB_TEST_POSTGRES_URL=postgresql+psycopg://USER@127.0.0.1:15450/ailab_migrations`, `AILAB_TEST_S3_URL=http://127.0.0.1:19450`, `AILAB_TEST_S3_ACCESS_KEY`, `AILAB_TEST_S3_SECRET_KEY`. Only throwaway services: the rest of the suite wipes the PG migration DB. S3 creates a unique temporary bucket per test and removes it. The PostgreSQL backup drill additionally requires CREATE/DROP DATABASE privileges on the throwaway server. CI builds real MinIO from a fixed official source archive with a checked SHA256 (`scripts/ci_minio.sh`), caches the binary, and runs it as an independent job on loopback. It no longer depends on an unavailable Docker Hub tag or on Python-suite success. This is a protocol fixture, not a production storage recommendation. See [CI](../../../.github/CI.md).

`test_gc_queries_have_indexed_plans` proves index eligibility on both SQL dialects (PG disables sequential scans for the empty fixture); it is not a production-scale latency benchmark. Backup's SQLite drill starts a separate application process, verifies file bytes and reads a conversation after restoring to an empty directory. The GC interleaving holds the real user lock while a completion freezes its object and a collector waits.

Performance probe: `python scripts/measure_storage.py --mib 50` then `--mib 100`. On this macOS checkout, 50/100 MiB stream write + copy + read took 0.15/0.38 s, Python allocation peak 148,697 bytes, RSS growth 278,528/688,128 bytes; two objects used 100/200 MiB of disk plus tiny metadata. These are local adapter measurements, not target-node or Broker/Worker acceptance.

Controlled-fetch / deny-all acceptance on disposable real providers:

```bash
AILAB_TEST_DOCKER=1 AILAB_TEST_OSB_URL=http://127.0.0.1:18090 \
AILAB_OSB_NETWORK_MODE=kubernetes AILAB_OSB_USE_SERVER_PROXY=true \
AILAB_SANDBOX_IMAGE=your-nonroot-python-image AILAB_TEST_PUBLIC_FETCH=1 \
pytest tests/contracts/test_sandbox_transfer.py tests/contracts/test_sandbox_lifecycle.py tests/contracts/test_controlled_fetch.py -q
```

Use `sidecar` for a compatible Docker/runc OpenSandbox server; never retry without policy automatically. `test_controlled_fetch` performs real public downloads, a small GitHub archive and an offline PyPI install. Malicious archives are generated locally and injected through Broker HTTP. Skill path retrieval uses the real sandbox; its API receiver is a stub (the independent API/scan tests cover persistence).

`test_sandbox_network.py` additionally takes `AILAB_TEST_SANDBOX_TCP_TARGETS` (comma-separated `IPv4:port`, public/Service/Pod/node/metadata) and `AILAB_TEST_SANDBOX_UDP_TARGET` (a disposable node UDP echo responder). Check those endpoints from the node first: refusal from an already dead endpoint proves nothing. Capture node ICMP rule counters, and verify the ICMP test fails with the rule removed. This test is IPv4-only and does not establish dual-stack isolation.

CI now verifies JUnit groups, so configured Redis/PG/S3 contracts cannot silently skip. Heavy Docker/Redis recovery, actual Office image smoke/skills recovery, ClamAV and browser Keycloak contracts run in `.github/workflows/real-components.yml` weekly or manually with disposable fixtures. Every selected heavy case must execute successfully (zero skips). The minimal Docker image exercises the provider contract only; the Office job separately builds the real image and runs `test_skill_sandbox.py`. Explicitly configure OpenSandbox/gVisor before claiming their acceptance. See [.github/CI.md](../../../.github/CI.md) for coverage and resource boundaries.

For Docker-backed OpenSandbox, `[egress] mode = "dns+nft"` is required: DNS-only filtering does not stop literal-IP connections even with `NetworkPolicy(default_action="deny")`. Verify live TCP/UDP targets from outside the sandbox before running the negative checks; a dead target proves nothing. Do not reuse the same `broker_owner` across direct Docker and OpenSandbox reconcilers sharing a daemon.

The five-service browser fixture also supports real backends. Keep every service disposable, including the PostgreSQL database (migrated and populated by the test). Models remain deterministic mocks, and scanning remains the test scanner. Use a dedicated Redis instance because fixture cleanup deletes lease keys:

```bash
AILAB_E2E=1 AILAB_E2E_SANDBOX_PROVIDER=opensandbox \
AILAB_E2E_SANDBOX_IMAGE=your-current-office-image \
AILAB_OSB_DOMAIN=127.0.0.1:18090 AILAB_OSB_USE_SERVER_PROXY=true \
AILAB_E2E_DATABASE_URL=postgresql+psycopg://USER@127.0.0.1:15450/disposable_e2e \
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16450/13 \
pytest e2e/test_portal.py::test_a_spreadsheet_opens_in_the_preview_once_it_is_done_and_can_be_revised -ra
```

Supply the disposable server key through `AILAB_OSB_API_KEY`. For direct Docker use `AILAB_E2E_SANDBOX_PROVIDER=docker` and optionally `AILAB_SANDBOX_RUNTIME=runsc`. Unknown providers and missing real images fail explicitly; defaults still use mock + isolated SQLite. The real Office image and Broker checkout must have identical skills; `skill_index_mismatch` requires rebuilding the image, never bypassing attestation.
