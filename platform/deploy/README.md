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

### k3s + OpenSandbox + gVisor controlled fetch

Provision every sandbox node before accepting workloads:

```bash
# Install the complete pinned gVisor distribution, including gvisor-bin/.
# https://gvisor.dev/docs/user_guide/install/
sudo bash k3s/install-runsc.sh
sudo systemctl restart k3s
sudo k3s crictl info  # runsc.options.ConfigPath must be /etc/containerd/runsc.toml
kubectl apply -k deploy/overlays/lite
```

The shim reads `k3s/runsc-options.toml`: `overlay2=root:self,size=4g` synchronously caps each container's writable root (including `/workspace`). Namespace `LimitRange` adds a 5 GiB ephemeral-storage limit and 64 MiB request per container; kubelet eviction/log rotation is supplemental, not a synchronous write quota. The root cap does not bound mounted writable volumes. Do not add user-writable hostPath/PVC/emptyDir mounts without a separate enforced capacity bound. The two layers are node/namespace owned; `sandbox_disk_bytes` alone is not enforcement. Existing sandboxes retain their original runtime options and must drain before acceptance.

`install-runsc.sh` also installs an idempotent systemd rule dropping outbound ICMP from the IPv4 Pod CIDR `10.42.0.0/16`. kube-router otherwise accepts ICMP before namespace default-deny; `CAP_NET_RAW` removal and gVisor `net-raw=false` do not disable datagram ping sockets. This node rule affects **all pods** in that CIDR (including diagnostic ping), leaves TCP/UDP to NetworkPolicy, and must match `k3s/config.yaml`. This deployment is IPv4-only; changing Pod CIDRs/CNI or enabling IPv6 requires renewed boundary acceptance. The installer refuses to overwrite a custom containerd template; reconcile its import explicitly. It does not restart a live node itself.

Install OpenSandbox's **release-1.1.1** base CRDs and controller charts from `opensandbox-group/OpenSandbox` (controller image `opensandbox/controller:release-1.1.1`; upstream defaults may use an unreachable regional registry). Set controller limits to the chart's 500m CPU / 128 MiB. SDK and host lifecycle server are pinned to 1.1.1. `deploy.sh` now requires this controller, validates node overlay config, applies the sandbox overlay, imports both platform and sandbox images, and invokes `osb_up.sh` with `OSB_RUNTIME=kubernetes`. It refuses an existing Docker-mode server config. A healthy server can be reused only when its tracked PID, command line and startup config digest match; a health response alone is not Kubernetes validation. It never installs/reconfigures a live cluster automatically.

For manual setup, copy `k3s/opensandbox.example.toml` into a private config, inject a private API key, and install `k3s/opensandbox-template.yaml` at the configured path. The template fixes RuntimeClass `gvisor`, uid/gid 10001, drops all capabilities, disables privilege escalation and service-account token mounts. The server stays on node loopback; lite Broker uses host networking. With `OSB_RUNTIME=kubernetes`, `osb_up.sh` creates the same files in its private OSB home on first use. Protect the key/config and restrict host ports as described above.

Set Broker `AILAB_OSB_NETWORK_MODE=kubernetes` (lite overlay does this). In this explicit mode, OSB receives **no sidecar policy**, because Server 1.1.1 rejects gVisor's unsupported NAT combination. Namespace default-deny plus the node ICMP rule provide isolation. The default remains `sidecar`; a failed create is never retried without policy. In `secure`, Kubernetes mode additionally requires operator acceptance `AILAB_OSB_KUBERNETES_ISOLATION_VERIFIED=true` after running the real transfer/network contracts. This flag records external infrastructure acceptance; it cannot enforce or continuously attest CNI policy. Removing policy/rules after acceptance invalidates the contract. Set `AILAB_OSB_USE_SERVER_PROXY=true` only when the Broker cannot directly route to Pod endpoints (e.g. the Mac test client).

Acceptance and bounded performance evidence: [controlled-network review](../../docs/reviews/2026-10-24-controlled-network.md). The current Mac Compose deployment remains independent of this path.

### Incremental Portal releases

Build from a clean fixed-commit export at a stable path, with the tag passed only
as an output name: `docker build -f platform/deploy/portal.Dockerfile -t ailab-portal:COMMIT .`.
The Dockerfile installs dependencies before copying source and exports only the
Next.js standalone runtime plus static assets. Normal local builds retain `next start`.
If a `public/` directory is introduced, copy it into the runtime stage too.

Compare against the actual deployed commit before selecting components. A Portal-only
change can reuse the existing platform and Office images and recreate only Portal;
verify image IDs, fixed source, effective configuration and HTTP/static assets.
Backend/configuration/migration changes require the full preservation/backup and
service lifecycle. Documentation/test-only changes need no runtime rollout. Do not
invalidate caches using versioned COPY paths, rebuild unchanged images, restart
unchanged services, or rerun an already accepted identical source tree. Keep CI and
recovery evidence; reducing redundant work does not remove release gates.
