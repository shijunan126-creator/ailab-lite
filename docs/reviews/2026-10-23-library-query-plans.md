# Library query plans — 2026-10-23

Checked SQLite and a throwaway PostgreSQL 16 instance. No application/developer database was queried or modified.
This is a synthetic query comparison, not an end-to-end production benchmark.

## Workload

100,000 rows per table, 20 users, 50 projects per user, 20% ungrouped files/conversations;
about 14% image MIME types and 3% starred files. Query one user's 5,000 rows and limit lists to 50.
Run ANALYZE after each index stage; PostgreSQL VACUUM ANALYZE after loading to enable realistic index-only scans.
Elapsed figures below are mean client query times over ten warm repetitions (ms), and are illustrative.
The timestamp distribution deliberately includes stretches of non-image rows to expose filtering work.

Existing indexes: user_id, project_id, and (user_id, created_at); conversations use the corresponding updated_at ordering in the application.
The count queries do not depend on the ordering timestamp. New indexes are amended into the unreleased 0008 migration:

```sql
CREATE INDEX ix_files_user_starred ON files (user_id, starred_at)
  WHERE starred_at IS NOT NULL;
CREATE INDEX ix_files_user_project_created ON files (user_id, project_id, created_at);
CREATE INDEX ix_files_images_user_created ON files (user_id, created_at)
  WHERE content_type LIKE 'image/%';
CREATE INDEX ix_conversations_user_project_live ON conversations (user_id, project_id)
  WHERE deleted_at IS NULL AND project_id IS NOT NULL;
```

## Queries and evidence

- **All:** `WHERE user_id = :user ORDER BY created_at DESC LIMIT 50` continues to use `ix_files_user_created` on both engines. No additional index needed.
- **Starred:** `WHERE user_id = :user AND starred_at IS NOT NULL ORDER BY starred_at DESC LIMIT 50` changes from scanning the user's files and sorting to `ix_files_user_starred` in order. SQLite about 7.3 → 0.14 ms; PostgreSQL about 0.78 → 0.15 ms.
- **Images:** `WHERE user_id = :user AND content_type LIKE 'image/%' ORDER BY created_at DESC LIMIT 50` changes to `ix_files_images_user_created` in order. SQLite about 6.7 → 0.13 ms. PostgreSQL EXPLAIN ANALYZE: about 0.87 → 0.055 ms, buffers about 4363 → 52, with 4285 non-image rows previously removed by the filter.
- **Project:** `WHERE user_id = :user AND project_id = :project ORDER BY created_at DESC LIMIT 50` uses `ix_files_user_project_created`. SQLite drops its temporary ORDER BY B-tree (about 0.21 → 0.13 ms). PostgreSQL replaces a BitmapAnd across the single-column indexes with one composite bitmap scan; for the small 65-row project it still chooses a cheap sort (about 0.28 → 0.20 ms).
- **q:** `WHERE user_id = :user AND filename ILIKE '%report%' ORDER BY created_at DESC LIMIT 50` (SQLite uses lower/LIKE) remains bounded by the user index. A normal filename B-tree cannot accelerate the leading wildcard. No speculative text/trigram index or search-semantic change added.
- **File counts:** `SELECT project_id, count(*) FROM files WHERE user_id = :user AND project_id IS NOT NULL GROUP BY project_id` uses the covering `ix_files_user_project_created` on SQLite (about 8.3 → 0.56 ms), and an Index Only Scan on PostgreSQL (about 0.97 → 0.53 ms).
- **Conversation counts:** same grouping with `deleted_at IS NULL` uses `ix_conversations_user_project_live`. SQLite removes its GROUP BY temporary B-tree (about 8.6 → 0.68 ms); PostgreSQL uses an Index Only Scan (about 1.07 → 0.40 ms).

The project detail query now restricts each count subquery to that project, and the project list excludes NULL project groups that can never join a project.

SQLite requires a literal `LIKE 'image/%'` predicate to match this partial index; a bound `LIKE ?` does not match it.
The API uses SQLAlchemy's constant literal_column for that fixed MIME prefix. User-provided search text stays bound and escaped.
The original all-files ordering index is retained: the project composite cannot provide creation-time ordering across projects.
These four indexes add write/storage cost only for the measured paths (starred/images/live-project conversations are partial).

Migration upgrade/downgrade, existing referenced data, and metadata drift are checked on both database engines by the normal migration suite.


## T18 — immutable versions and trash (2026-10-23)

Worktree `arch/w2-file-versions`, based on `609c1d3`; SQLite and throwaway PostgreSQL 16 on port 15490.
100,000 file rows / 20 users / five immutable versions per lineage (20,000 heads); 1/11 lineages trashed,
1/7 images and 1/31 starred. Run ANALYZE on SQLite and VACUUM ANALYZE on PG before ten warm repetitions.
These are synthetic database checks, not production latency or object-store/RSS acceptance.

The API selects page IDs first, fetches only that page's rows, then groups retained version counts in one query
(no per-file count queries). All, image, starred, project, cursor, and trash page selection use SQLite
COVERING INDEX / PostgreSQL Index Only Scan (Heap Fetches: 0). Full FileOut rows still require the bounded
second fetch. Search retains substring semantics and reads candidate names through the live-head index;
no claim of index-only substring search. Project/version counts are index-only too. The fixed `is_head = 1`
and image LIKE predicates use SQL literals; caller-controlled q remains escaped and bound.

SQLite needs `is_head`, `deleted_at`, and `purged_at` in the covering keys even though the predicate fixes them;
the image index also covers content_type. Merely adding the partial predicate produced `USING INDEX` and
opened the table cursor. The final covering plan has no table cursor. Both engines maintain unique
(lineage_id, version), plus at most one head via a unique partial index. The retained-version index includes
version for bounded trimming even when a lineage accumulates many purged tombstones; parent_id is indexed
for hard-delete FK cleanup. Existing indexes remain available for quota and project deletion.

Ten new indexes add about 12 MiB on this PostgreSQL workload (all file indexes about 26 MiB). Four live-list
indexes contain only active heads, two trash/reaper indexes only trashed heads; no new service/dependency.
A version write updates one head, inserts one row and these indexes, and tombstones excess retained versions.
Restore copies objects through Storage.copy without materializing full file bytes in the API; it writes a
full independent object and derived prefix. Reaper selects at most 200 expired heads per pass, rechecks each
under the user lock, deletes rows in batches of 200, and deletes objects after commit. Empty-trash fixes a
request-start cutoff so concurrent new deletions cannot make it run forever. Real S3 latency, copy disk/RSS,
and retry/GC after process death remain integration acceptance with E/T09; existing object deletion is best effort.

Exact queries and raw plans follow (LIMIT 20 on the trim probe bounds display; runtime trims all excess rows).

### SQLite

**all** (0.054 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_live_created (user_id=?)
```

**images** (0.048 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%' ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_live_images (user_id=?)
```

**starred** (0.040 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL ORDER BY starred_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_live_starred (user_id=? AND starred_at>?)
```

**project** (0.049 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND project_id='p0-0' ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_live_project (user_id=? AND project_id=?)
```

**q** (0.089 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND lower(filename) LIKE lower('%report%') ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING INDEX ix_files_live_created (user_id=?)
```

**trash** (0.046 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NOT NULL ORDER BY deleted_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_trash (user_id=? AND deleted_at>?)
```

**project_counts** (0.145 ms mean, ten warm repetitions)
```sql
SELECT project_id,count(*) FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND project_id IS NOT NULL GROUP BY project_id
```
```text
SEARCH files USING COVERING INDEX ix_files_live_project (user_id=? AND project_id>?)
```

**version_counts** (0.033 ms mean, ten warm repetitions)
```sql
SELECT lineage_id,count(*) FROM files WHERE lineage_id IN ('f000000','f000100') AND purged_at IS NULL GROUP BY lineage_id
```
```text
SEARCH files USING COVERING INDEX ix_files_lineage_retained (lineage_id=? AND purged_at=?)
```

**history** (0.056 ms mean, ten warm repetitions)
```sql
SELECT * FROM files WHERE user_id='u0' AND lineage_id='f000000' ORDER BY version DESC
```
```text
SEARCH files USING INDEX uq_files_lineage_version (lineage_id=?)
```

**next_version** (0.024 ms mean, ten warm repetitions)
```sql
SELECT max(version) FROM files WHERE lineage_id='f000000'
```
```text
SEARCH files USING COVERING INDEX uq_files_lineage_version (lineage_id=?)
```

**purge_due** (0.148 ms mean, ten warm repetitions)
```sql
SELECT user_id,lineage_id FROM files WHERE is_head=1 AND deleted_at IS NOT NULL AND purge_at<='2026-12-01' ORDER BY purge_at LIMIT 200
```
```text
SEARCH files USING INDEX ix_files_purge_due (purge_at<?)
```

**parent_cleanup** (12.363 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE parent_id IN ('f000000','f000100')
```
```text
SCAN files
```

**quota** (2.579 ms mean, ten warm repetitions)
```sql
SELECT sum(size) FROM files WHERE user_id='u0' AND (status!='rejected' OR deleted_at IS NOT NULL) AND purged_at IS NULL
```
```text
SEARCH files USING INDEX ix_files_user_created (user_id=?)
```

**trim** (0.041 ms mean, ten warm repetitions)
```sql
SELECT * FROM files WHERE lineage_id='f000000' AND purged_at IS NULL ORDER BY version DESC LIMIT 20 OFFSET 10
```
```text
SEARCH files USING INDEX ix_files_lineage_retained (lineage_id=? AND purged_at=?)
```

**cursor** (0.058 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND (created_at,id)<('2026-10-02','f086400') ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
SEARCH files USING COVERING INDEX ix_files_live_created (user_id=? AND (created_at,id)<(?,?))
```

**project_delete** (0.471 ms mean, ten warm repetitions)
```sql
SELECT DISTINCT lineage_id FROM files WHERE user_id='u0' AND project_id='p0-0' LIMIT 200
```
```text
SEARCH files USING INDEX ix_files_user_project_created (user_id=? AND project_id=?)
USE TEMP B-TREE FOR DISTINCT
```

### PostgreSQL 16

**all** (0.181 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.29..3.61 rows=50 width=16) (actual time=0.009..0.013 rows=50 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_live_created on files  (cost=0.29..60.02 rows=899 width=16) (actual time=0.008..0.010 rows=50 loops=1)
        Index Cond: (user_id = 'u0'::text)
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=164
Planning Time: 0.646 ms
Execution Time: 0.019 ms
```

**images** (0.191 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%' ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.28..4.26 rows=50 width=16) (actual time=0.006..0.010 rows=50 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_live_images on files  (cost=0.28..10.54 rows=129 width=16) (actual time=0.006..0.008 rows=50 loops=1)
        Index Cond: (user_id = 'u0'::text)
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=6
Planning Time: 0.083 ms
Execution Time: 0.014 ms
```

**starred** (0.166 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL ORDER BY starred_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.28..4.80 rows=30 width=16) (actual time=0.003..0.006 rows=30 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_live_starred on files  (cost=0.28..4.80 rows=30 width=16) (actual time=0.003..0.005 rows=30 loops=1)
        Index Cond: (user_id = 'u0'::text)
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=3
Planning Time: 0.065 ms
Execution Time: 0.009 ms
```

**project** (0.191 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND project_id='p0-0' ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.29..4.47 rows=9 width=16) (actual time=0.012..0.016 rows=50 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_live_project on files  (cost=0.29..4.47 rows=9 width=16) (actual time=0.012..0.014 rows=50 loops=1)
        Index Cond: ((user_id = 'u0'::text) AND (project_id = 'p0-0'::text))
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=3
Planning Time: 0.069 ms
Execution Time: 0.020 ms
```

**q** (0.200 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND filename ILIKE '%report%' ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.29..162.33 rows=50 width=16) (actual time=0.006..0.042 rows=50 loops=1)
  Buffers: shared hit=52
  ->  Index Scan Backward using ix_files_live_created on files  (cost=0.29..2913.89 rows=899 width=16) (actual time=0.006..0.040 rows=50 loops=1)
        Index Cond: ((user_id)::text = 'u0'::text)
        Filter: ((filename)::text ~~* '%report%'::text)
        Buffers: shared hit=52
Planning:
  Buffers: shared hit=3
Planning Time: 0.070 ms
Execution Time: 0.046 ms
```

**trash** (0.166 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NOT NULL ORDER BY deleted_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.28..3.45 rows=50 width=16) (actual time=0.005..0.009 rows=50 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_trash on files  (cost=0.28..5.80 rows=87 width=16) (actual time=0.005..0.006 rows=50 loops=1)
        Index Cond: (user_id = 'u0'::text)
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=3
Planning Time: 0.051 ms
Execution Time: 0.012 ms
```

**project_counts** (0.270 ms mean, ten warm repetitions)
```sql
SELECT project_id,count(*) FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND project_id IS NOT NULL GROUP BY project_id
```
```text
GroupAggregate  (cost=0.29..67.76 rows=100 width=14) (actual time=0.032..0.138 rows=5 loops=1)
  Group Key: project_id
  Buffers: shared hit=12
  ->  Index Only Scan using ix_files_live_project on files  (cost=0.29..62.27 rows=899 width=6) (actual time=0.005..0.089 rows=909 loops=1)
        Index Cond: ((user_id = 'u0'::text) AND (project_id IS NOT NULL))
        Heap Fetches: 0
        Buffers: shared hit=12
Planning:
  Buffers: shared hit=6
Planning Time: 0.062 ms
Execution Time: 0.143 ms
```

**version_counts** (0.167 ms mean, ten warm repetitions)
```sql
SELECT lineage_id,count(*) FROM files WHERE lineage_id IN ('f000000','f000100') AND purged_at IS NULL GROUP BY lineage_id
```
```text
GroupAggregate  (cost=0.42..9.19 rows=10 width=16) (actual time=0.014..0.015 rows=2 loops=1)
  Group Key: lineage_id
  Buffers: shared hit=10
  ->  Index Only Scan using ix_files_lineage_retained on files  (cost=0.42..9.04 rows=10 width=8) (actual time=0.012..0.013 rows=10 loops=1)
        Index Cond: ((lineage_id = ANY ('{f000000,f000100}'::text[])) AND (purged_at IS NULL))
        Heap Fetches: 0
        Buffers: shared hit=10
Planning Time: 0.038 ms
Execution Time: 0.019 ms
```

**history** (0.230 ms mean, ten warm repetitions)
```sql
SELECT * FROM files WHERE user_id='u0' AND lineage_id='f000000' ORDER BY version DESC
```
```text
Index Scan Backward using uq_files_lineage_version on files  (cost=0.29..15.39 rows=1 width=320) (actual time=0.004..0.004 rows=5 loops=1)
  Index Cond: ((lineage_id)::text = 'f000000'::text)
  Filter: ((user_id)::text = 'u0'::text)
  Buffers: shared hit=4
Planning:
  Buffers: shared hit=28
Planning Time: 0.051 ms
Execution Time: 0.008 ms
```

**next_version** (0.162 ms mean, ten warm repetitions)
```sql
SELECT max(version) FROM files WHERE lineage_id='f000000'
```
```text
Result  (cost=1.11..1.12 rows=1 width=4) (actual time=0.004..0.004 rows=1 loops=1)
  Buffers: shared hit=3
  InitPlan 1 (returns $0)
    ->  Limit  (cost=0.29..1.11 rows=1 width=4) (actual time=0.002..0.002 rows=1 loops=1)
          Buffers: shared hit=3
          ->  Index Only Scan Backward using uq_files_lineage_version on files  (cost=0.29..4.39 rows=5 width=4) (actual time=0.002..0.002 rows=1 loops=1)
                Index Cond: ((lineage_id = 'f000000'::text) AND (version IS NOT NULL))
                Heap Fetches: 0
                Buffers: shared hit=3
Planning:
  Buffers: shared hit=3
Planning Time: 0.061 ms
Execution Time: 0.009 ms
```

**purge_due** (0.220 ms mean, ten warm repetitions)
```sql
SELECT user_id,lineage_id FROM files WHERE is_head=1 AND deleted_at IS NOT NULL AND purge_at<='2026-12-01' ORDER BY purge_at LIMIT 200
```
```text
Limit  (cost=0.28..10.99 rows=155 width=19) (actual time=0.007..0.028 rows=200 loops=1)
  Buffers: shared hit=4
  ->  Index Only Scan using ix_files_purge_due on files  (cost=0.28..10.99 rows=155 width=19) (actual time=0.007..0.020 rows=200 loops=1)
        Index Cond: (purge_at <= '2026-12-01 00:00:00+08'::timestamp with time zone)
        Heap Fetches: 0
        Buffers: shared hit=4
Planning:
  Buffers: shared hit=3
Planning Time: 0.044 ms
Execution Time: 0.035 ms
```

**parent_cleanup** (0.141 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE parent_id IN ('f000000','f000100')
```
```text
Index Scan using ix_files_parent_id on files  (cost=0.29..12.61 rows=1 width=8) (actual time=0.004..0.004 rows=0 loops=1)
  Index Cond: ((parent_id)::text = ANY ('{f000000,f000100}'::text[]))
  Buffers: shared hit=4
Planning Time: 0.031 ms
Execution Time: 0.006 ms
```

**quota** (1.034 ms mean, ten warm repetitions)
```sql
SELECT sum(size) FROM files WHERE user_id='u0' AND (status!='rejected' OR deleted_at IS NOT NULL) AND purged_at IS NULL
```
```text
Aggregate  (cost=1876.61..1876.62 rows=1 width=8) (actual time=1.356..1.356 rows=1 loops=1)
  Buffers: shared hit=1067
  ->  Bitmap Heap Scan on files  (cost=58.76..1864.20 rows=4963 width=4) (actual time=0.211..1.217 rows=5000 loops=1)
        Recheck Cond: ((user_id)::text = 'u0'::text)
        Filter: ((purged_at IS NULL) AND (((status)::text <> 'rejected'::text) OR (deleted_at IS NOT NULL)))
        Heap Blocks: exact=1062
        Buffers: shared hit=1067
        ->  Bitmap Index Scan on ix_files_user_id  (cost=0.00..57.51 rows=4963 width=0) (actual time=0.136..0.136 rows=5000 loops=1)
              Index Cond: ((user_id)::text = 'u0'::text)
              Buffers: shared hit=5
Planning:
  Buffers: shared hit=3
Planning Time: 0.042 ms
Execution Time: 1.363 ms
```

**trim** (0.181 ms mean, ten warm repetitions)
```sql
SELECT * FROM files WHERE lineage_id='f000000' AND purged_at IS NULL ORDER BY version DESC LIMIT 20 OFFSET 10
```
```text
Limit  (cost=15.38..18.40 rows=1 width=320) (actual time=0.007..0.007 rows=0 loops=1)
  Buffers: shared hit=4
  ->  Index Scan Backward using uq_files_lineage_version on files  (cost=0.29..15.38 rows=5 width=320) (actual time=0.006..0.007 rows=5 loops=1)
        Index Cond: ((lineage_id)::text = 'f000000'::text)
        Filter: (purged_at IS NULL)
        Buffers: shared hit=4
Planning Time: 0.039 ms
Execution Time: 0.012 ms
```

**cursor** (0.178 ms mean, ten warm repetitions)
```sql
SELECT id FROM files WHERE user_id='u0' AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL AND (created_at,id)<('2026-10-02','f086400') ORDER BY created_at DESC,id DESC LIMIT 50
```
```text
Limit  (cost=0.29..4.00 rows=50 width=16) (actual time=0.005..0.010 rows=50 loops=1)
  Buffers: shared hit=3
  ->  Index Only Scan Backward using ix_files_live_created on files  (cost=0.29..38.63 rows=517 width=16) (actual time=0.005..0.007 rows=50 loops=1)
        Index Cond: ((user_id = 'u0'::text) AND (ROW(created_at, id) < ROW('2026-10-02 00:00:00+08'::timestamp with time zone, 'f086400'::text)))
        Heap Fetches: 0
        Buffers: shared hit=3
Planning:
  Buffers: shared hit=3
Planning Time: 0.068 ms
Execution Time: 0.017 ms
```

**project_delete** (0.435 ms mean, ten warm repetitions)
```sql
SELECT DISTINCT lineage_id FROM files WHERE user_id='u0' AND project_id='p0-0' LIMIT 200
```
```text
Limit  (cost=184.89..185.14 rows=51 width=8) (actual time=0.255..0.330 rows=200 loops=1)
  Buffers: shared hit=222
  ->  Unique  (cost=184.89..185.14 rows=51 width=8) (actual time=0.255..0.322 rows=200 loops=1)
        Buffers: shared hit=222
        ->  Sort  (cost=184.89..185.02 rows=51 width=8) (actual time=0.255..0.276 rows=996 loops=1)
              Sort Key: lineage_id
              Sort Method: quicksort  Memory: 40kB
              Buffers: shared hit=222
              ->  Bitmap Heap Scan on files  (cost=4.94..183.44 rows=51 width=8) (actual time=0.077..0.183 rows=1000 loops=1)
                    Recheck Cond: (((user_id)::text = 'u0'::text) AND ((project_id)::text = 'p0-0'::text))
                    Heap Blocks: exact=213
                    Buffers: shared hit=222
                    ->  Bitmap Index Scan on ix_files_user_project_created  (cost=0.00..4.93 rows=51 width=0) (actual time=0.066..0.066 rows=1000 loops=1)
                          Index Cond: (((user_id)::text = 'u0'::text) AND ((project_id)::text = 'p0-0'::text))
                          Buffers: shared hit=9
Planning Time: 0.040 ms
Execution Time: 0.341 ms
```

File indexes: 26 MB.
