# Single-node deployment

`base/` contains apps, database, object-volume, queue, private search, public gateway and ingress NetworkPolicies. Each resource group is independently applicable (`kubectl apply -k platform/deploy/base/database`, `.../objects`, `.../apps`, `.../gateway`). Upgrading app images does not restart PostgreSQL, recreate PVCs or rebuild Portal. There is a slot at `base/scanner/` for C's ClamAV resources and Worker-only policy.

Render before applying:

```bash
kubectl kustomize platform/deploy/overlays/dev
kubectl kustomize platform/deploy/overlays/lite
```

Lite, from the repo root: `sudo ./deploy.sh` (or `./deploy.sh --dry-run`). It creates secrets/catalog first, applies `platform/deploy/overlays/lite`, waits for readiness and runs the existing smoke check. `--no-portal` allows app-only updates; Portal's host process remains independently upgradeable. Only `platform-public:30080` is public: it allow-lists `/v1/`, `/healthz`, `/readyz`. `/internal`, `/metrics`, `/docs`, Agent, Worker and Broker have no NodePort. API/Broker clients use cluster Service DNS. The extra nginx gateway has a 48 MiB limit, 10m CPU request, no buffering of SSE; measure actual RSS/latency on the target host.

The dev overlay uses PostgreSQL 16 (4 GiB database PVC) and a 20 GiB object PVC. Create namespace/platform secrets/models, then `bash platform/scripts/k8s_database_secret.sh`, then apply the overlay. PostgreSQL credentials live in their own Secret; the image includes psycopg. Provide a reachable OpenSandbox Service at `opensandbox:8090`. Access the public API through a port-forward or an explicit ingress to `platform-public`, never to `platform-api`.

Lite is a **compatibility layer**, not a multi-node deployment: SQLite stays at `/data/platform.db` on the existing `platform-data` PVC; Broker alone uses `hostNetwork` to reach host OpenSandbox at `127.0.0.1:8090`. SQLite upgrades necessarily share API lifetime. Moving to independent PostgreSQL requires an explicit data migration, not switching overlays on a live database. Object PVC and app lifecycle are separate even in lite. Local RWO volumes constrain all users of the volume to one node; PostgreSQL or S3 does not itself make the Broker horizontally scalable.

K3s includes the NetworkPolicy controller: base denies unsolicited pod ingress, then allows explicit service edges. Egress remains open for model calls, DNS and external S3. Host-network traffic is outside portable pod NetworkPolicy semantics: **the lite host firewall must restrict 8081 and 8090 to the node/cluster, with no public access**. The public gateway never routes to Broker or `/internal`.

Capacity parameters live in each overlay's PVC/config patches: lite defaults to 40 GiB, dev to 20 GiB. Keep `AILAB_PROVISIONED_STORAGE_BYTES` equal to provisioned object capacity; library quota is still 20 GiB/user. API warns when current users × quota exceeds that capacity; it never blocks startup. Budget extra space for SQLite/PG, derived assets, workspace snapshots, staging, logs and backups. Existing PVCs may need storage-class-supported expansion; applying a larger request does not prove disk expansion succeeded. Check filesystem and PVC capacity before accepting uploads. Backups go to a separate disk/host.

`deploy/overlays/lite` at repo root continues to own sandbox namespace/runtime quotas; app overlays do not own sandbox lifecycle. `k8s_down.sh` and `deploy.sh down` intentionally delete the whole platform namespace, including PVCs: use the group apply paths for ordinary upgrades.

SearXNG is deployed independently under `base/search/`. Agent uses `http://platform-searxng:8080`, explicitly configured in `platform-config`; only Agent pods may enter its ClusterIP port. It receives no platform credentials and has no public route. Its upstream resource budget is 100m CPU / 128 MiB requested, 500m / 512 MiB limited; the earlier 2 vCPU / 8 GiB measurements predate this service and do not establish the combined stack's capacity. The inherited `latest` image and static SearXNG key still need release hardening and real search/load acceptance (tracked in the progress roadmap).
