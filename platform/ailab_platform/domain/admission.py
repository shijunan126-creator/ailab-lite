"""FIFO among eligible requests in each reserved capacity class.

SINGLE BROKER ONLY. The caller serializes admission and reservation; a Redis TTL
owner lock prevents accidental replicas, not a distributed scheduler. A user at
its cap does not block other users. Render capacity cannot be borrowed by Work.
"""

import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from ..contracts.sandbox import SandboxSpec
from ..ports.capacity import CapacityProvider


@dataclass(frozen=True)
class LeasePrincipal:
    user_id: str
    run_id: str
    system: bool = False  # set by trusted Worker code, never by a model or request body


@dataclass(frozen=True)
class AdmissionDecision:
    status: str  # admitted | queued | rejected
    request_id: str
    position: int | None = None
    reason: str | None = None
    eta: float | None = None


@dataclass
class _Ticket:
    principal: LeasePrincipal
    spec: SandboxSpec
    deadline: float


class AdmissionPolicy:
    def __init__(
        self,
        capacity: CapacityProvider,
        leases: Callable[[], list[dict]],
        user_limit: Callable[[], int],
        queue_limit: Callable[[], int],
        render_ttl: Callable[[], int],
        burst_watermark: Callable[[], float] = lambda: 1.0,
    ) -> None:
        self.capacity_provider, self.leases = capacity, leases
        self.user_limit, self.queue_limit, self.render_ttl = user_limit, queue_limit, render_ttl
        self.burst_watermark = burst_watermark
        self.queue: OrderedDict[str, _Ticket] = OrderedDict()

    def utilization(self, live: list[dict]) -> float:
        """How full the general (non-render) pool is, by slots or memory, whichever is tighter."""
        cap = self.capacity_provider.capacity()
        slots, memory = cap.sandboxes - cap.render_slots, cap.memory - cap.render_memory
        work = [x for x in live if x["spec"]["profile"] != "render"]
        if slots <= 0 or memory <= 0:
            return 1.0
        return max(len(work) / slots, sum(x["spec"]["memory"] for x in work) / memory)

    def effective_user_limit(self, spec: SandboxSpec, live: list[dict]) -> int:
        """Everyone may hold a first sandbox; more only while the general pool stays under the watermark.

        Judged as if the extra sandbox were already running, so a user cannot push the pool past it. The
        limit shrinks back to one as the pool fills, without touching sandboxes that already exist.
        """
        hard = self.user_limit()
        cap = self.capacity_provider.capacity()
        slots, memory = cap.sandboxes - cap.render_slots, cap.memory - cap.render_memory
        work = [x for x in live if x["spec"]["profile"] != "render"]
        mark = self.burst_watermark()
        if hard <= 1 or slots <= 0 or memory <= 0:
            return 1
        if (len(work) + 1) / slots > mark or (sum(x["spec"]["memory"] for x in work) + spec.memory) / memory > mark:
            return 1
        return hard

    def _reason(self, ticket: _Ticket, live: list[dict]) -> str | None:
        p, spec, cap = ticket.principal, ticket.spec, self.capacity_provider.capacity()
        render = spec.profile == "render"
        if not render and sum(x["user_id"] == p.user_id for x in live) >= self.effective_user_limit(spec, live):
            return "user_limit"
        same = [x for x in live if (x["spec"]["profile"] == "render") == render]
        slots = cap.render_slots if render else cap.sandboxes - cap.render_slots
        memory = cap.render_memory if render else cap.memory - cap.render_memory
        if len(same) >= slots or sum(x["spec"]["memory"] for x in same) + spec.memory > memory:
            return "capacity"
        return None

    def admit(
        self, principal: LeasePrincipal, spec: SandboxSpec, *, request_id: str | None = None, wait_seconds: float = 0
    ) -> AdmissionDecision:
        now = time.monotonic()
        request_id = request_id or uuid.uuid4().hex
        if not principal.user_id or not principal.run_id:
            return AdmissionDecision("rejected", request_id, reason="invalid_principal")
        if spec.profile == "render" and (not principal.system or spec.ttl > self.render_ttl() or spec.max_lifetime > self.render_ttl()):
            return AdmissionDecision("rejected", request_id, reason="render_requires_short_system_job")
        cap = self.capacity_provider.capacity()
        slots = cap.render_slots if spec.profile == "render" else cap.sandboxes - cap.render_slots
        memory = cap.render_memory if spec.profile == "render" else cap.memory - cap.render_memory
        if slots <= 0 or spec.memory > memory:
            return AdmissionDecision("rejected", request_id, reason="spec_exceeds_capacity")
        live = self.leases()
        if request_id not in self.queue:
            # Admission needs no queue slot. In particular a full Work queue cannot
            # deny an otherwise free reserved render slot.
            self.queue = OrderedDict((k, v) for k, v in self.queue.items() if v.deadline > now)
            ticket = _Ticket(principal, spec, now + max(0, wait_seconds))
            older_eligible = any(t.spec.profile == spec.profile and self._reason(t, live) is None for t in self.queue.values())
            if not older_eligible and self._reason(ticket, live) is None:
                return AdmissionDecision("admitted", request_id)
            if wait_seconds <= 0:
                return AdmissionDecision("rejected", request_id, reason="queue_deadline")
            if len(self.queue) >= self.queue_limit():
                return AdmissionDecision("rejected", request_id, reason="queue_full")
            self.queue[request_id] = ticket
        elif self.queue[request_id].deadline <= now:
            self.queue.pop(request_id)
            return AdmissionDecision("rejected", request_id, reason="queue_deadline")
        ticket = self.queue[request_id]
        eligible = next(
            (
                key
                for key, t in self.queue.items()
                if t.spec.profile == spec.profile and (t.deadline > now or key == request_id) and self._reason(t, live) is None
            ),
            None,
        )
        if eligible == request_id:
            self.queue.pop(request_id)
            return AdmissionDecision("admitted", request_id)
        if ticket.deadline <= now:
            self.queue.pop(request_id)
            return AdmissionDecision("rejected", request_id, reason="queue_deadline")
        position = list(self.queue).index(request_id) + 1
        return AdmissionDecision("queued", request_id, position=position, reason=self._reason(ticket, live) or "fifo")

    def cancel(self, request_id: str) -> None:
        self.queue.pop(request_id, None)

    def status(self, run_id: str) -> dict:
        for position, (request_id, ticket) in enumerate(self.queue.items(), 1):
            if ticket.principal.run_id == run_id:
                return {
                    "status": "queued",
                    "request_id": request_id,
                    "position": position,
                    "remaining_seconds": max(0, ticket.deadline - time.monotonic()),
                }
        return {"status": "absent"}
