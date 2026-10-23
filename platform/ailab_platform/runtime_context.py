"""Execution identity propagated across service calls, never inferred from a Run id."""

from contextvars import ContextVar

attempt_id_var: ContextVar[str | None] = ContextVar("runtime_attempt", default=None)
delivery_id_var: ContextVar[str | None] = ContextVar("runtime_delivery", default=None)


class RuntimeContextMiddleware:
    """Forward caller execution identity across Broker -> API calls."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        attempt = attempt_id_var.set(headers.get(b"x-run-attempt", b"").decode() or None)
        delivery = delivery_id_var.set(headers.get(b"x-delivery-id", b"").decode() or None)
        try:
            await self.app(scope, receive, send)
        finally:
            attempt_id_var.reset(attempt)
            delivery_id_var.reset(delivery)
