# ADR 0001 · Single-node scope and defaults

Status: accepted (2026-10-23). Revisit when the open questions in the gap review §7.1 get an owner's answer.

Context: [gap review](../../reviews/2026-10-23-single-node-architecture-gap.md) §7.1 lists questions that change acceptance gates. Development proceeds on the defaults below; each is a configuration value or a documented contract, so changing it later doesn't require a redesign.

| Question | Decision | Where it lives |
|---|---|---|
| Who uses the single node | A small trusted pilot (one department, tens of users). The `secure` profile is the gate for any shared deployment: real scanner, no mock sandbox, no default credentials. | `AILAB_PROFILE` |
| Concurrency | Agent runs up to `agent_concurrency=3` Runs at once with `chat_reserved_slots=1`; one Run per conversation at a time. Sandbox capacity stays a separate limit in the Broker. | `config.py` |
| Token quota semantics | User daily/monthly quota is **hard**: each model call reserves `max_output_tokens` × weight before it starts and settles to the actual usage. The per-Run token budget stays **soft**: it stops the next step, and the answer in flight may finish over budget. Period boundaries use `quota_timezone` (default UTC). Fallback models draw from the same pool at their own weight. | `quota.py`, ADR text |
| Sandbox lifetime | Keep "one sandbox per turn, destroyed after the turn" plus the three-day `/workspace` snapshot. It preserves files, not agent execution state; resuming a Run uses the runtime checkpoint. The target's 15-minute idle sandbox is not implemented. | Broker, [03 §5](../03-Agent与沙箱设计.md) |
| Retention | Files keep at most 10 versions; deleted files go to a trash for 30 days; conversations deleted by the user are purged after 30 days; run events and previews follow their owner. Admins do not read user content by default; every admin action is audited. | `config.py`, reaper |
| Base repository | `ailab-lite` is the base. The target architecture is frozen under `docs/architecture/` (see SOURCE.md); multi-node repositories are derived from this one. | this repo |
| Office templates and golden tasks | The skills manifest, versioning and evaluation harness are built now; company templates and golden samples are plugged in when supplied. | `images/sbx-office/skills` |

Consequences: the deployment is honest about its limits (single Broker process, Redis without persistence as a queue that can be rebuilt from the outbox, SQLite or a single PostgreSQL), and every limit above is enforced in code rather than by convention.
