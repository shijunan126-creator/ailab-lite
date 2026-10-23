# Implementation status

This matrix starts with the 2026-10-23 Broker lease work on `feat/arch-hardening`. The full target comparison remains in [the gap review](../reviews/2026-10-23-single-node-architecture-gap.md); entries here describe only changes accepted by this branch.

| Capability | State | Evidence / boundary |
|---|---|---|
| F4 / T06: Broker restart ownership, lease state CAS, reconcile, heartbeat, maximum lifetime | Implemented with mock and Redis fault coverage | `platform/tests/faults/test_broker_recovery.py`; Docker/OSB lifecycle contract suites remain opt-in, not real-provider acceptance. |
| Broker part of T08: static total/memory budget, per-user cap, eligible FIFO, bounded deadline queue, short render reserve | Implemented for one Broker | `domain/admission.py`, `ports/capacity.py`; no distributed scheduler or durable waiting queue. |
| SandboxProvider / LeaseStore extension boundary | Implemented | `contracts/sandbox.py`, `ports/lease_store.py`; resource labels rebuild a flushed store. Disk enforcement and real Docker/OSB sizing remain open. |
| Agent heartbeat / recoverable lease-loss runtime handling | Contract supplied; runtime integration pending | HTTP renew/status and `AsyncBrokerClient` helpers; `lease_lost` / `lease_expired` return 410. |
| Direct Agent → secure execd; idle sandbox reuse | Deferred by single-node scope | [ADR 0003](adr/0003-broker-exec-proxy-and-leases.md), [ADR 0001](adr/0001-single-node-scope.md). |
