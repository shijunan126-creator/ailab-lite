# ADR 0007 · Public network access by controlled fetch and injection

Status: accepted for the single-node implementation (2026-10-24).

## Context

Users need to read pages and GitHub issues with sources, study public repositories, download images and documents for artifacts, install dependencies and run experiments, and create or fetch Skills. The sandbox runs untrusted code (the model's and the repository's) next to the user's materials. Sandboxes run on Docker + gVisor (`runsc`) with `--network none`; the Agent already ran `web_search`/`read_url` outside the sandbox.

The target architecture (06 §1.5, 08) lets the sandbox reach only package mirrors through an OpenSandbox egress sidecar and NetworkPolicy. On this deployment the OpenSandbox egress sidecar does not combine with gVisor (status matrix), and giving a `runsc` container a Docker network would need a new proxy service, an internal network, disabled inter-container traffic and DNS control. Each of those can fail open: a misconfigured network gives the sandbox the VM's routes.

## Decision

The sandbox keeps `--network none`. Public bytes are fetched by the Agent and streamed into the sandbox by the Broker:

| Need | Tool | Fetched by | Opened by |
|---|---|---|---|
| page, GitHub issue + comments | `read_url` | Agent (text only, bounded); since [ADR 0021](0021-isolated-browser-rendering-for-read-url.md) pages are rendered by the isolated crawler behind the egress proxy | model context, marked untrusted |
| file, image, document | `download_url` | Agent → disk spool → Broker → sandbox | sandbox |
| public repository source | `fetch_repo` | Agent (GitHub tarball) | sandbox (`tarfile` data filter + count/size limits) |
| Python dependencies | `fetch_packages` | Agent resolves with PyPI JSON metadata, downloads files with PyPI sha256 | sandbox `pip install --no-index` |
| personal Skill | `save_skill` | API stores for the run's owner as PENDING | Worker scan + review policy |

Controls enforced in code:

- **Address**: only http(s) on 80/443, no credentials in URLs; every DNS answer must be public, including IPv6 forms that embed IPv4 (mapped, NAT64, 6to4, Teredo). The connection goes to the checked address through a custom httpcore network backend, so a rebinding DNS answer cannot redirect it; each redirect is validated the same way. No environment proxy, no cookies, no credentials. Image search downloads use the same client.
- **Provenance**: `read_url`/`download_url` accept a URL only if it appeared in a user message or a `web_search`/`read_url` result, or is on public code/package hosts (GitHub, PyPI). The model cannot build `https://any-host/?q=<conversation data>`. Sandbox output and the model's own text never add sources.
- **Budget**: per file 100 MiB; per run 512 MiB and 120 external requests; archive expansion 30 000 entries / 1 GiB; resolution 60 packages, no direct-URL requirements, no backtracking. Downloads are written once to a private temp file and streamed; nothing holds the whole file in memory.
- **No external writes**: only GET; no tokens for GitHub/PyPI, so only public data is reachable and nothing can be pushed, commented or submitted.
- **Skills**: external Skills are files in `/workspace`, read with `read_file`, never loadable through `load_skill`. `save_skill` writes only the run owner's personal skills: `create` refuses a name in use (including pending saves) or a built-in name; `update` adds a version to the owner's own skill and keeps the old version. The API records the claimed name; if the scanned `SKILL.md` declares a different name it is marked invalid instead of shadowing another skill. Saved skills go through the same scan and approval path as uploads.

## Consequences

- Package install is limited to PyPI (wheels preferred, sdists built inside the sandbox). npm, apt, git history, private repositories and authenticated APIs are not available. A repository's own network calls fail inside the sandbox.
- One extra copy of each download exists on the Agent's disk and on the Broker's spool for the duration of the transfer; both are removed on success, failure and cancellation.
- Residual leakage channels remain and are not claimed closed: search queries go to public engines; choosing among links on an attacker-controlled page, choosing which public packages or repositories to request, and requesting public GitHub paths are low-bandwidth signals that a page author could observe in some cases (e.g. package download statistics). The run request budget bounds them. Fully removing them needs per-run data classification (no external fetch after reading internal data), which is a product decision, not implemented here.
- `sandbox_disk_bytes` alone is advisory: enforcement is node-owned runsc configuration. The accepted Compose runtime and the validated k3s path both cap the root overlay at 4 GiB; other Docker installations and mounted writable volumes require explicit enforcement and acceptance.

## 2026-10-24: Kubernetes enforcement path

Docker retains `--network none`. On the validated single-node IPv4 k3s path, OpenSandbox 1.1.1 + gVisor rejects the sidecar `networkPolicy` because gVisor has no required iptables NAT support. The Broker now has an explicit `osb_network_mode=kubernetes`: creation omits that unsupported sidecar policy and relies on the sandbox namespace's deny-all NetworkPolicy plus node enforcement. The default stays `sidecar`; creation errors never trigger an automatic retry with isolation removed. Secure profile requires explicit infrastructure acceptance (`osb_kubernetes_isolation_verified`), which is an operator assertion, not continuous attestation.

Real probes found that kube-router allows ICMP before namespace policy, even with capabilities dropped; gVisor datagram ping sockets remain usable without `CAP_NET_RAW`. Node provisioning therefore installs a persistent raw-table ICMP drop for the configured IPv4 Pod CIDR. This also disables diagnostic ping from other pods in that CIDR. TCP/UDP stay under the CNI's default-deny rules, including tested node destinations. IPv6, another Pod CIDR, CNI, multi-node topology, host networking or writable volume changes require separate acceptance; the lite preflight refuses different/dual-stack Pod CIDRs. Replies to authorized inbound execd requests necessarily remain allowed.

Node-owned containerd `ConfigPath` points to runsc's `overlay2=root:self,size=4g`; real writes fail with `ENOSPC` at 4 GiB under a 512 MiB memory cap. Namespace defaults additionally bound ephemeral storage to 5 GiB per container (kubelet eviction, not synchronous quota). Mounted volumes are outside the root-overlay cap and must not be exposed as unbounded writable storage. Deployment fixes include importing the sandbox image into containerd and refusing a healthy but Docker-mode OpenSandbox server. Evidence and remaining limits are in the [updated review](../../reviews/2026-10-24-controlled-network.md).
