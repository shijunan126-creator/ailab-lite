"""Bounded Agent pools with a dedicated Chat lane and durable job handback."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Response
from fastapi.responses import JSONResponse

from .. import metrics, obs
from ..catalog import get_catalog
from ..clients import AsyncPlatformClient, ClientError
from ..config import settings
from ..contracts.jobs import JobEnvelope, RunStatus
from ..deps import metrics_auth
from ..metrics import metrics_response
from ..obs import NO_REQUEST, RequestContextMiddleware, request_id_var, setup_logging
from ..queue import Message, get_queue, run_lock_key
from ..runtime_context import attempt_id_var
from .loop import AgentLoop
from .prompts import system_prompt

setup_logging("agent")
log = logging.getLogger("ailab.agent")
_task: asyncio.Task | None = None


CONSUMER = f"agent-{socket.gethostname()}-{os.getpid()}"


def visibility_seconds() -> int:
    """Transport redelivery is independent of run duration; durable claims fence duplicates."""
    return settings.job_lease_seconds


async def _with_job_heartbeat(work, platform, job_id, token, *, lease_started_at=None):
    async def renew():
        clock = asyncio.get_running_loop()
        lease = settings.job_lease_seconds
        interval = min(60, lease / 3)
        margin = min(30, lease / 5)
        # Start conservatively at ownership acquisition; a renew may have committed even
        # when its response is lost, but only a confirmed response extends our deadline.
        last_success = clock.time() if lease_started_at is None else lease_started_at
        delay = interval
        failures = 0
        while True:
            remaining = last_success + lease - margin - clock.time()
            if remaining <= 0:
                raise TimeoutError("job renewal safety deadline exceeded")
            await asyncio.sleep(min(delay, remaining))
            remaining = last_success + lease - margin - clock.time()
            if remaining <= 0:
                raise TimeoutError("job renewal safety deadline exceeded")
            started = clock.time()
            try:
                async with asyncio.timeout(min(10, lease / 6, remaining)):
                    await platform.job_action(job_id, "renew", token=token)
            except Exception as exc:
                if isinstance(exc, ClientError) and exc.status == 409:
                    raise  # stale ownership must stop immediately, even during an outage
                failures += 1
                delay = min(15, interval / 4) * 2 ** min(failures - 1, 4)
                delay = min(delay, interval)
                log.warning("job renew failed; retrying within ownership deadline job=%s failures=%d", job_id, failures)
            else:
                # The API sets expiry before responding, so response latency is not lease time.
                last_success = started
                failures = 0
                delay = interval

    processing = asyncio.create_task(work)
    heartbeat = asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait({processing, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            await heartbeat
        await processing
    finally:
        processing.cancel()
        heartbeat.cancel()
        await asyncio.gather(processing, heartbeat, return_exceptions=True)


async def handle_message(loop: AgentLoop, queue, msg: Message, topic: str | None = None) -> None:
    """Process one run message. On shutdown (CancelledError) the run is handed back to the queue."""
    topic = topic or settings.run_queue
    platform = AsyncPlatformClient()
    token = None
    job_id = msg.payload.get("job_id")
    owned_runtime = None
    trace_context = getattr(obs, "traceparent_var", None)
    trace_token = None
    execution_token = None
    try:
        if "schema_version" in msg.payload:
            envelope = JobEnvelope.model_validate(msg.payload)
            if envelope.kind != "run":
                raise ValueError("non-run envelope on Agent stream")
            if trace_context is not None:
                trace_token = trace_context.set(envelope.traceparent)
            lease_started_at = asyncio.get_running_loop().time()
            claim = await platform.job_action(job_id, "claim", consumer=CONSUMER)
            if claim["state"] != "claimed":
                await asyncio.to_thread(queue.ack, topic, msg)
                return
            token = claim["token"]
            msg.payload["attempt"] = claim["attempt"]
            msg.payload["attempt_id"] = claim["attempt_id"]
            execution_token = attempt_id_var.set(claim["attempt_id"])
            queue_wait = getattr(metrics, "QUEUE_WAIT_SECONDS", None)
            if queue_wait is not None and envelope.enqueued_at is not None:
                queue_wait.labels(envelope.mode).observe(max(0, (datetime.now(UTC) - envelope.enqueued_at).total_seconds()))
        else:
            # Every Run has a durable job staged in its creating transaction; a bare message has no attempt to fence.
            log.warning("dropping run message without a durable job run=%s", msg.payload.get("run_id"))
            await asyncio.to_thread(queue.ack, topic, msg)
            return
        if loop is None:
            owned_runtime = loop = AgentLoop(platform=platform)
        await _with_job_heartbeat(
            _handle_message(loop, queue, msg, platform, topic=topic, durable=bool(token)),
            platform,
            job_id,
            token,
            lease_started_at=lease_started_at,
        )
        if token:
            state = await platform.get_run_state(msg.payload["run_id"])
            if (
                state["status"] not in RunStatus.TERMINAL | {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
                and state.get("job_id", job_id) == job_id
            ):
                raise RuntimeError("run is not terminal; retain durable job")
            await platform.job_action(job_id, "ack", token=token)
            await asyncio.to_thread(queue.ack, topic, msg)
    except asyncio.CancelledError:
        if token:
            # Releasing durable ownership precedes transport ACK; API failure leaves the lease intact.
            try:
                await platform.job_action(job_id, "release", token=token)
                await asyncio.to_thread(queue.ack, topic, msg)
            except Exception:
                log.exception("durable handback failed; lease expiry will recover the job")
        raise
    finally:
        if execution_token is not None:
            attempt_id_var.reset(execution_token)
        if owned_runtime is not None:
            await owned_runtime.broker.aclose()
        await platform.aclose()
        if trace_token is not None:
            trace_context.reset(trace_token)


async def _handle_message(loop: AgentLoop, queue, msg: Message, platform, *, topic=None, durable=False) -> None:
    topic = topic or settings.run_queue

    async def ack_transport():
        if not durable:
            await asyncio.to_thread(queue.ack, topic, msg)

    run_id = msg.payload.get("run_id")
    if not run_id:
        log.warning("dropping malformed run message: %s", msg.payload)
        await ack_transport()
        return

    # A message re-queued at shutdown starts a fresh delivery count, so it carries its own.
    attempt = int(msg.payload["attempt"]) if durable else max(msg.deliveries, int(msg.payload.get("attempt", 1)))

    lock = run_lock_key(run_id)
    token = "durable" if durable else await asyncio.to_thread(queue.acquire_lock, lock, settings.job_lease_seconds)
    if token is None:
        log.warning("run %s is already being executed elsewhere; dropping duplicate message", run_id)
        await ack_transport()
        return

    log.info("picked run %s (attempt %d)", run_id, attempt)
    try:
        try:
            await loop.process(run_id, attempt=attempt, attempt_id=msg.payload.get("attempt_id"))
        except asyncio.CancelledError:
            # Shutting down (rolling deploy, SIGTERM). Waiting for the visibility timeout would
            # leave the run stalled for ~16 minutes, so hand it to the next agent right away.
            if durable:
                raise
            try:
                await asyncio.to_thread(queue.enqueue, topic, {"run_id": run_id, "attempt": attempt + 1})
                await ack_transport()
                log.info("run %s handed back to the queue for another agent", run_id)
            except Exception:
                log.exception("could not hand run %s back; it will be redelivered after the visibility timeout", run_id)
            raise
        except Exception:
            log.exception("run %s crashed", run_id)
            try:
                await platform.set_run_status(run_id, RunStatus.FAILED, error="agent crashed unexpectedly")
            except Exception:
                log.exception("could not mark crashed run %s failed", run_id)
        await ack_transport()
    finally:
        if not durable:
            await asyncio.to_thread(queue.release_lock, lock, token)


async def consume_forever() -> None:
    # Fixed consumers take capacity before dequeue: no unbounded task or message prefetch.
    shared = asyncio.Semaphore(settings.agent_concurrency)

    async def pool(topic, slots):
        async def consume(index):
            while True:
                async with shared:
                    queue = get_queue()
                    try:
                        msg = await asyncio.to_thread(queue.dequeue, topic, f"{CONSUMER}-{index}", 1, visibility_seconds())
                    except Exception:
                        log.exception("run queue unavailable; retrying")
                        await asyncio.sleep(1)
                        continue
                    if msg is None:
                        continue
                    rid = request_id_var.set(str(msg.payload.get("request_id") or msg.payload.get("run_id") or NO_REQUEST))
                    try:
                        await handle_message(None, queue, msg, topic)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("could not finish handling run message")
                    finally:
                        request_id_var.reset(rid)

        await asyncio.gather(*(consume(index) for index in range(slots)))

    tasks = [
        asyncio.create_task(pool(f"{settings.run_queue}:chat", settings.chat_reserved_slots)),
        asyncio.create_task(pool(f"{settings.run_queue}:work", settings.agent_concurrency - settings.chat_reserved_slots)),
    ]
    group = asyncio.gather(*tasks, return_exceptions=True)
    # If a dependency ignores cancellation past the drain deadline, retain an observer
    # for its eventual result; the durable lease remains the recovery authority.
    group.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
    try:
        await asyncio.shield(group)
    finally:
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=settings.drain_seconds)
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.sleep(0)
            log.error("Agent drain deadline exceeded; unacknowledged durable jobs will recover")
        if group.done():
            await group


def begin_drain(loop: asyncio.AbstractEventLoop) -> None:
    """Stop taking runs and hand the current one back, now - not when the web server gets round to shutting down.

    On SIGTERM uvicorn first waits for open connections (up to the pod's grace period) and only then runs the
    lifespan shutdown. Until then the consumer would keep taking new runs, and the pod is killed in the middle of
    one, which then sits with a dead consumer until the visibility timeout (~16 minutes).
    """
    if _task and not _task.done():
        log.info("SIGTERM: handing the current run back and taking no new ones")
        loop.call_soon_threadsafe(_task.cancel)


def _chain_sigterm(loop: asyncio.AbstractEventLoop) -> None:
    """Run begin_drain on SIGTERM, then whatever handler was there (uvicorn's, which starts its own shutdown)."""
    previous = signal.getsignal(signal.SIGTERM)

    def handler(signum, frame) -> None:
        begin_drain(loop)
        if callable(previous):
            previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread (e.g. a test client): nothing to chain
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _task
    settings.validate_secrets("agent")
    settings.ensure_dirs()
    system_prompt()  # raises if the skill file is missing
    _task = asyncio.create_task(consume_forever(), name="agent-consumer")
    _chain_sigterm(asyncio.get_running_loop())
    catalog = get_catalog()  # logs which models are hidden for want of a key
    log.info("agent service ready sandbox=%s models=%s", settings.sandbox_provider, [m.id for m in catalog.available()])
    yield
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="AILab Agent Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware, service="agent")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "agent", "models": [m.id for m in get_catalog().available()]}


@app.get("/readyz")
def readyz() -> Response:
    try:
        queue_ok = get_queue().ping()
    except Exception:
        queue_ok = False
    running = bool(_task and not _task.done())
    ok = queue_ok and running
    return JSONResponse(status_code=200 if ok else 503, content={"ok": ok, "queue": queue_ok, "consumer_running": running})


@app.get("/metrics", include_in_schema=False, dependencies=[Depends(metrics_auth)])
def metrics_endpoint() -> Response:
    return metrics_response()
