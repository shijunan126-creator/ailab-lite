"""Prometheus metrics: a deliberately small set that answers operational questions.

* Is work piling up?             ailab_queue_depth, ailab_runs_in_state
* How long do things take?       ailab_run_seconds, ailab_sandbox_acquire_seconds, ailab_llm_call_seconds, ailab_tool_seconds
* What fails?                    ailab_runs_total{status="failed"}, ailab_llm_calls_total{outcome}, ailab_tool_calls_total{ok}
* What does it cost?             ailab_model_tokens_total{model,kind}, ailab_artifact_bytes_total
* Is a service healthy/sized?    process_resident_memory_bytes (prometheus_client's process collector), ailab_sandbox_leases_active

Every service is a single process and serves its own /metrics (guarded by the service token), so the default
registry is the right one. Label values that come from outside (tool names the model invents, model ids)
are mapped onto a fixed set: an unbounded label would grow Prometheus without limit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from starlette.responses import Response

log = logging.getLogger("ailab.metrics")

_FAST = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_SLOW = (1, 2.5, 5, 10, 20, 40, 60, 120, 300, 600, 900)

HTTP_REQUESTS = Counter("ailab_http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
HTTP_SECONDS = Histogram("ailab_http_request_seconds", "Time to first response byte", ["service", "route"], buckets=_FAST)

CLIENT_CALLS = Counter("ailab_client_calls_total", "Browser call outcomes", ["kind", "outcome"])
CLIENT_CALL_SECONDS = Histogram("ailab_client_call_seconds", "Browser call latency", ["kind", "outcome"], buckets=_SLOW)

RUNS = Counter("ailab_runs_total", "Runs that entered a status", ["status"])
CONVERSATION_TITLES = Counter("ailab_conversation_titles_total", "Title outcomes and fallback causes", ["outcome", "reason"])
RUN_SECONDS = Histogram("ailab_run_seconds", "Start-to-finish time of a run", ["status"], buckets=_SLOW)
MODEL_TOKENS = Counter("ailab_model_tokens_total", "Model tokens as reported by the upstream", ["model", "kind"])
PROMPT_CACHE_TOKENS = Counter(
    "ailab_prompt_cache_tokens_total", "Reported prompt and cached tokens by request type", ["mode", "kind", "token_type"]
)


def record_prompt_cache(mode: str, kind: str, usage: dict) -> None:
    if mode not in {"chat", "work", "memory"} or kind not in {"main", "title", "compaction", "memory", "draft"}:
        raise ValueError("unknown prompt cache metric label")
    for token_type, key in (("prompt", "prompt_tokens"), ("cached", "cached_tokens")):
        PROMPT_CACHE_TOKENS.labels(mode, kind, token_type).inc(max(0, int(usage.get(key, 0) or 0)))


WEB_READ_SECONDS = Histogram(
    "ailab_web_read_seconds",
    "read_url time by path (static, rendered, static_fallback) and outcome",
    ["path", "outcome"],
    buckets=(0.25, 0.5, 1, 2.5, 5, 10, 20, 40),
)
MEMORY_RECALL = Counter("ailab_memory_recall_total", "History recall calls by outcome", ["outcome"])
MEMORY_EMBEDDING = Counter("ailab_memory_embedding_total", "Embedding endpoint calls by outcome", ["outcome"])
SKILL_DRAFTS = Counter("ailab_skill_drafts_total", "Background skill draft outcomes", ["outcome"])
MEMORY_GUARD_REJECTED = Counter("ailab_memory_guard_rejected_total", "Memory writes refused by the safety guard", ["reason", "source"])
QUOTA_REJECTIONS = Counter("ailab_quota_rejections_total", "Requests refused by a quota", ["kind"])
REAPER_ACTIONS = Counter("ailab_reaper_actions_total", "Things the reaper repaired", ["action"])

SANDBOX_ACQUIRE_SECONDS = Histogram(
    "ailab_sandbox_acquire_seconds", "Time to create a lease: sandbox start + input injection", buckets=_SLOW
)
SANDBOX_EXEC_SECONDS = Histogram("ailab_sandbox_exec_seconds", "Command execution in a sandbox", ["provider"], buckets=_SLOW)
SANDBOX_CAPACITY_REJECTIONS = Counter("ailab_sandbox_capacity_rejections_total", "Lease requests turned away because all slots were taken")
SANDBOX_ADMISSION_WAIT_SECONDS = Histogram("ailab_sandbox_admission_wait_seconds", "Time waiting for admission", buckets=_SLOW)
SANDBOX_EVICTIONS = Counter("ailab_sandbox_evictions_total", "Idle sandboxes reclaimed for a waiting request", ["reason"])
SANDBOX_RECONCILE_ACTIONS = Counter("ailab_sandbox_reconcile_actions_total", "Lease reconciliation actions", ["action"])
SANDBOX_PROVIDER_ERRORS = Counter("ailab_sandbox_provider_errors_total", "Sandbox provider errors", ["provider", "operation"])

ARTIFACT_BYTES = Counter("ailab_artifact_bytes_total", "Bytes delivered from sandboxes")
WORKSPACE_SNAPSHOTS_EXPIRED = Counter("ailab_workspace_snapshots_expired_total", "Workspace snapshots deleted by their TTL")

AGENT_RUN_SECONDS = Histogram("ailab_agent_run_seconds", "Time the Agent spent on one run", ["outcome"], buckets=_SLOW)
AGENT_RECOVERED = Counter("ailab_agent_recovered_total", "Runs restarted because an earlier attempt did not finish")
LLM_CALLS = Counter("ailab_llm_calls_total", "Model calls", ["model", "outcome"])
LLM_SECONDS = Histogram("ailab_llm_call_seconds", "Model call latency", ["model"], buckets=_SLOW)
MODEL_TTFT_SECONDS = Histogram("ailab_model_ttft_seconds", "Time to first effective model output", ["model"], buckets=_SLOW)
QUEUE_WAIT_SECONDS = Histogram("ailab_queue_wait_seconds", "Run creation to consumer start", ["mode"], buckets=_SLOW)
RUN_RESUMES = Counter("ailab_run_resumes_total", "Run resume requests", ["outcome"])
LEASE_HEARTBEAT_FAILURES = Counter("ailab_lease_heartbeat_failures_total", "Agent lease renew failures")
TOOL_CALLS = Counter("ailab_tool_calls_total", "Agent tool calls", ["tool", "ok"])
TOOL_SECONDS = Histogram("ailab_tool_seconds", "Agent tool latency", ["tool"], buckets=_SLOW)

FILES = Counter("ailab_files_processed_total", "Uploaded files the Worker finished with", ["outcome"])
FILE_SECONDS = Histogram("ailab_file_processing_seconds", "Worker time per uploaded file", buckets=_SLOW)
SCAN_EXHAUSTED = Counter("ailab_scan_exhausted_total", "Files that exhausted scan retries")

KNOWN_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "write_file",
        "exec",
        "exec_wait",
        "exec_kill",
        "deliver_file",
        "finish",
        "search_library",
        "read_library_file",
        "copy_library_file",
        "web_search",
        "read_url",
        "download_url",
        "fetch_repo",
        "fetch_packages",
        "save_skill",
        "memory",
        "recall_history",
    }
)


def tool_label(name: str) -> str:
    """Tool names come from the model; anything outside our own set is one label."""
    return name if name in KNOWN_TOOLS else "other"


def model_label(model_id: str) -> str:
    from .catalog import get_catalog

    return model_id if get_catalog().get(model_id) is not None else "other"


def observe_http(service: str, method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(service, method, route, str(status)).inc()
    HTTP_SECONDS.labels(service, route).observe(seconds)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ----------------------------------------------------------------------------- scrape-time gauges
class ApiStateCollector(Collector):
    """Values that live in the database / queue are read when Prometheus scrapes, not tracked incrementally."""

    def collect(self):
        from sqlalchemy import func, select

        from .config import settings
        from .db import SessionLocal
        from .models import OutboxMessage, Run, RunStatus, UsageReservation
        from .queue import get_queue

        runs = GaugeMetricFamily("ailab_runs_in_state", "Runs per status right now", labels=["status"])
        try:
            with SessionLocal() as db:
                counts = dict(db.execute(select(Run.status, func.count()).group_by(Run.status)).all())
            for status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED):
                runs.add_metric([status], counts.get(status, 0))
        except Exception:
            log.warning("could not read run counts for /metrics", exc_info=True)
        yield runs

        depth = GaugeMetricFamily("ailab_queue_depth", "Messages not yet acknowledged (waiting or in flight)", labels=["queue"])
        try:
            queue = get_queue()
            chat_depth = queue.length(f"{settings.run_queue}:chat")
            work_depth = queue.length(f"{settings.run_queue}:work")
            depth.add_metric(["runs"], chat_depth + work_depth)
            depth.add_metric(["chat"], chat_depth)
            depth.add_metric(["work"], work_depth)
            depth.add_metric(["files"], queue.length(settings.file_queue))
        except Exception:
            log.warning("could not read queue depth for /metrics", exc_info=True)
        yield depth

        backlog = GaugeMetricFamily("ailab_outbox_backlog", "Durable jobs awaiting consumer ACK")
        oldest = GaugeMetricFamily("ailab_outbox_oldest_seconds", "Age of the oldest unacknowledged durable job")
        unreconciled = GaugeMetricFamily("ailab_usage_unreconciled", "Usage reservations requiring reconciliation")
        try:
            with SessionLocal() as db:
                count = db.scalar(select(func.count()).select_from(OutboxMessage)) or 0
                first = db.scalar(select(OutboxMessage.created_at).order_by(OutboxMessage.created_at, OutboxMessage.id).limit(1))
                pending = (
                    db.scalar(select(func.count()).select_from(UsageReservation).where(UsageReservation.status == "unreconciled")) or 0
                )
            backlog.add_metric([], count)
            if first is not None and first.tzinfo is None:
                first = first.replace(tzinfo=UTC)
            oldest.add_metric([], max(0, (datetime.now(UTC) - first).total_seconds()) if first else 0)
            unreconciled.add_metric([], pending)
        except Exception:
            log.warning("could not collect durable-work health", exc_info=True)
        yield backlog
        yield oldest
        yield unreconciled


class BrokerStateCollector(Collector):
    def __init__(self, manager_getter):
        self._manager = manager_getter

    def collect(self):
        from .config import settings

        active = GaugeMetricFamily("ailab_sandbox_leases_active", "Sandbox leases currently held")
        capacity = GaugeMetricFamily("ailab_sandbox_capacity", "Sandbox slots this Broker will hand out")
        try:
            active.add_metric([], len(self._manager().active_leases()))
        except Exception:
            log.warning("could not count leases for /metrics", exc_info=True)
        capacity.add_metric([], settings.max_sandboxes)
        states = GaugeMetricFamily("ailab_sandbox_leases_by_state", "Desired leases by state", labels=["state"])
        try:
            from collections import Counter

            from .ports.lease_store import LEASE_STATES

            counts = Counter(x["state"] for x in self._manager().store.all())
            for state in LEASE_STATES:
                states.add_metric([state], counts[state])
        except Exception:
            log.warning("could not count lease states for /metrics", exc_info=True)
        yield active
        yield capacity
        yield states


_registered: set[str] = set()


def register_once(key: str, collector: Collector) -> None:
    if key not in _registered:
        REGISTRY.register(collector)
        _registered.add(key)


def observe_web_read(path: str, outcome: str, seconds: float) -> None:
    WEB_READ_SECONDS.labels(path if path in ("static", "rendered", "static_fallback") else "other", outcome).observe(seconds)


GENUI_BLOCKS = Counter("ailab_genui_blocks_total", "OpenUI blocks by completion outcome", ["mode", "outcome"])
GENUI_COMPONENTS = Counter("ailab_genui_components_total", "OpenUI component occurrences", ["component"])
GENUI_ANSWERS = Counter("ailab_genui_answers_total", "Agent step answers with and without UI", ["mode", "has_ui"])
GENUI_CLIENT = Counter("ailab_genui_client_total", "Client rendering and action outcomes", ["outcome"])
GENUI_FIRST_RENDER_SECONDS = Histogram(
    "ailab_genui_first_render_seconds", "Client time to first UI render", buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10)
)


def record_genui(text: str, mode: str, *, max_blocks: int = 8, max_block_chars: int = 32000) -> dict:
    from .domain.answer_parts import inspect, split

    mode = "chat" if mode == "chat" else "work"
    report = inspect(text, max_block_chars=max_block_chars)
    GENUI_ANSWERS.labels(mode, "true" if report["blocks"] else "false").inc()
    for index, block in enumerate(part for part in split(text) if part["kind"] == "ui"):
        outcome = (
            "over_limit"
            if index >= max_blocks
            else "oversize"
            if len(block["text"]) > max_block_chars
            else "closed"
            if block["closed"]
            else "unclosed"
        )
        GENUI_BLOCKS.labels(mode, outcome).inc()
    for component, count in report["components"].items():
        GENUI_COMPONENTS.labels(component).inc(count)
    return report
