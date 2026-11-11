"""The Agent Service loop: model -> tools -> sandbox -> artifact.

One run is one turn of a conversation (or a standalone task). The answer text streams to the user as it is
generated; a sandbox is only leased when the model first uses a tool that needs one, so a plain chat turn never
waits for a sandbox. In a conversation the sandbox workspace is restored at lease time and saved at the end,
and office files written to /workspace/outputs get rendered previews while the turn is still running.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from .. import internal_skills, metrics, skillfiles
from ..adapters.runtime import NON_REPLAYABLE, UNCERTAIN_EXEC, fresh_state, restored_state
from ..catalog import Catalog, ModelSpec, get_catalog
from ..clients import AsyncBrokerClient, AsyncPlatformClient, ClientError
from ..config import settings
from ..contracts.genui import CATALOG_VERSION, FORMAT
from ..contracts.jobs import RunStatus
from ..contracts.usage import UsageStatus
from ..domain.answer_parts import for_history
from ..domain.answer_parts import inspect as inspect_answer
from ..domain.document_tools import catalog_version, permitted
from ..llm import INVALID_ARGUMENTS_KEY, LLMClient, LLMError, ModelCall, ToolCall, llm_for, model_call
from ..model_policy import ModelPolicy, estimator
from ..paths import OUTPUTS
from ..runtime_context import attempt_id_var, delivery_id_var
from ..skill_registry import BASE_SKILLS, resolve_name
from ..telemetry import span
from ..title_text import normalize_title
from . import discovery, net_tools, subtasks, watchdog
from .background_exec import background_exec, command_handle, command_text
from .budgets import BUDGET_WRAP_NOTE, REVIEW_NOTE, BudgetPause, ContextPause, QuotaPause, WorkBudget
from .checkpoint_budget import CAPACITY_NOTE, bounded_checkpoint
from .context import ContextManager
from .context import _summary_input as _summary_input
from .context_band import CANNOT_FIT_RESULT, ContextBand, admit_tool_result, batch_budget
from .context_usage import record_usage, request_tokens
from .document_tools import CONTRACTS as DOCUMENT_CONTRACTS
from .document_tools import ERRORS as DOCUMENT_ERRORS
from .document_tools import argument_error as document_argument_error
from .document_tools import failure as document_failure
from .file_dependencies import FileDependencyPending
from .multimodal import attachment_content, has_image_part, input_content
from .prompts import (
    load_skill,
    personal_header,
    render_skill_mentions,
    skills,
    system_prompt_for,
    user_prompt,
)
from .skill_modules import ModuleVisibility, read_module
from .skill_modules import identity as module_identity
from .skill_verification import verify_sandbox_skills
from .stuck import STUCK_ERROR, STUCK_NOTE, compact_log, detect_loop, observation
from .tools import SANDBOX_TOOLS, ToolContext, ToolError, execute_tool, tool_result_message, tools_for_chat, tools_for_turn, ui_components

log = logging.getLogger("ailab.agent")

T = TypeVar("T")
CANCEL_POLL_SECONDS = 2.0
SANDBOX_RETRY_SECONDS = 3.0
DELTA_FLUSH_SECONDS = 0.25  # how often streamed text is written as an agent.delta event
DELTA_FLUSH_CHARS = 120
PREVIEW_AFTER = {"bash", "bash_wait", "bash_kill", "write", "edit", "run_subtasks"}  # tools that can change files in /workspace/outputs
THINKING_KEEP_CHARS = 20_000  # of the reasoning stored with the run (the events keep all of it)
WRAP_UP_STEPS = 3  # this many steps before the limit the model is told to finish up
WRAP_UP_NOTE = "（平台提示：本轮只剩 {n} 次工具调用。请尽快收尾：生成并交付文件，然后用文字回复用户。）"
OUT_OF_STEPS_NOTE = "（平台提示：本轮的步数已用完。不要再调用工具，直接用文字告诉用户已经完成了什么、还有什么没完成、下一轮可以怎么继续。）"
FILE_TOOLS = frozenset(
    {"copy_library_file", "write", "edit", "bash", "deliver_file", "run_subtasks", *net_tools.NAMES}
)  # tools that change /workspace or its outputs
WORKSPACE_DEBOUNCE_SECONDS = 60  # between workspace snapshots while the run is busy
WORKSPACE_LAG_NOTE = "（平台提示：工作区快照落后于执行记录；部分文件变更可能丢失。请先检查工作区，不要假定已执行命令的文件仍在。）"
WORKSPACE_RECLAIMED_NOTE = (
    "（平台提示：沙箱因长时间没有操作，被平台回收给其他任务。回收时已保存工作区，其中运行的后台命令已终止；"
    "请先检查工作区，需要的后台命令请重新启动。）"
)
RECLAIM_WAIT_SECONDS = 240  # the Broker packs the workspace before deleting the sandbox
WORKSPACE_MISSING_NOTE = "（平台提示：此前在沙箱中的文件没有保存下来，工作区是空的。需要的文件请重新生成。）"


def _tools_for_run(run):
    if "_fixed_catalog" in run:
        return run["_fixed_catalog"]
    version = catalog_version(run.get("options"))
    catalog = [
        discovery.SEARCH,
        *(tools_for_chat(version) if (run.get("options") or {}).get("mode") == "chat" else tools_for_turn(version)),
    ]
    if not settings.client_js_enabled or (run.get("options") or {}).get("client") != "portal":
        catalog = [t for t in catalog if t["function"]["name"] != "run_js"]
    spec = get_catalog().get(run.get("model"))
    if not settings.genui_enabled or not spec or not spec.supports({"genui", "tools"}):
        catalog = [t for t in catalog if t["function"]["name"] != "ui_components"]
    if spec and not spec.supports({"vision"}):
        catalog = [t for t in catalog if t["function"]["name"] not in {"view_attachment", "view_workspace_file", "view_document"}]
    if spec and not spec.supports({"tools"}):
        return []
    if spec:
        input_budget = ModelPolicy.input_budget(spec)
        budget = min(input_budget, max(800, int(input_budget * 0.15)))
        try:
            return discovery.catalog_for_budget(catalog, budget, version)
        except ValueError as exc:
            raise LLMError(str(exc)) from exc
    return catalog


def web_sources(result: dict) -> list[dict]:
    from urllib.parse import urlsplit

    sources, seen = [], set()
    for item in result.get("results", [result]):
        if not isinstance(item, dict):
            continue
        # An image hit cites the page showing it, never the picture itself (that is only a signed /v1/web-images path).
        url = item.get("url") or item.get("page_url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or url in seen:
                continue
        except ValueError:
            continue
        seen.add(url)
        sources.append({"title": str(item.get("title") or parsed.hostname)[:200], "url": url, "site": parsed.hostname})
        if len(sources) == 10:
            break
    return sources


class RunPaused(Exception):
    pass


class LeaseInterrupted(Exception):
    pass


class RunCanceled(Exception):
    pass


class SteerArrived(Exception):
    """A user message arrived while waiting on something the run can safely stop waiting for."""


def _office_skills(value, *, keywords=False):
    text = json.dumps(value, ensure_ascii=False).lower()
    result = []
    formats = {
        "platform.pdf": (r"\.pdf\b|application/pdf", r"pdf"),
        "platform.docx": (r"\.docx?\b|application/(?:msword|vnd\.openxmlformats-officedocument\.wordprocessingml)", r"word|docx"),
        "platform.xlsx": (r"\.xlsx?\b|application/(?:vnd\.ms-excel|vnd\.openxmlformats-officedocument\.spreadsheetml)", r"excel|xlsx"),
        "platform.pptx": (
            r"\.pptx?\b|application/(?:vnd\.ms-powerpoint|vnd\.openxmlformats-officedocument\.presentationml)",
            r"pptx?|幻灯片|演示文稿",
        ),
    }
    for name, (files, words) in formats.items():
        if re.search(files + ("|" + words if keywords else ""), text):
            result.append(name)
    return result


def _carried_skills(messages, versions, mode, *, identity="version") -> set[str]:
    """Skills whose complete current version and mode remain in the carried-over messages."""
    calls: dict[str, str] = {}
    found = set()
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") != "load_skill":
                    continue
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    continue
                if isinstance(arguments, dict) and (name := resolve_name(str(arguments.get("name") or ""))) in versions:
                    calls[call.get("id")] = name
        elif message.get("role") == "tool" and (name := calls.get(message.get("tool_call_id"))):
            try:
                result = json.loads(message.get("content") or "")
            except ValueError:
                continue
            if isinstance(result, dict) and result.get("ok") and result.get("skill"):
                if (result.get(identity), result.get("mode")) == (versions[name], mode):
                    found.add(name)
    return found


def _full_skill_visible(turn, name):
    mode = (turn.run.get("options") or {}).get("mode", "work")
    if name in turn.personal:
        versions = {name: turn.personal[name]["file_id"]}
        return name in _carried_skills(turn.messages, versions, mode, identity="file_id")
    versions = {name: skills()[name]["version"]} if name in skills() else {}
    return name in _carried_skills(turn.messages, versions, mode)


_OFFICE_VIEW_SKILLS = {"platform.pptx", "platform.docx", "platform.xlsx"}

_FILE_TOOLS = {
    "ls",
    "read",
    "write",
    "edit",
    "grep",
    "find",
    "bash",
    "bash_wait",
    "bash_kill",
    "deliver_file",
    "download_url",
    "copy_library_file",
    "read_library_file",
    "search_library",
    "resolve_attachment",
    "view_attachment",
    "extract_text",
}


def _tool_office_skills(name, arguments, result):
    if name not in _FILE_TOOLS:
        return []

    def file_fields(value, in_collection=False):
        if isinstance(value, dict):
            return [
                v
                for k, item in value.items()
                for v in (
                    [item]
                    if k in {"filename", "path", "content_type", "sandbox_filename"} and isinstance(item, str)
                    else file_fields(item, True)
                    if k in {"files", "artifacts", "outputs"}
                    else file_fields(item, in_collection)
                    if isinstance(item, (dict, list))
                    else []
                )
            ]
        if isinstance(value, list):
            return [
                v
                for item in value
                for v in (file_fields(item, in_collection) if not isinstance(item, str) else [item] if in_collection else [])
            ]
        return []

    return _office_skills(file_fields([arguments, result]))


class _Turn:
    """Mutable state of one run while the loop works on it."""

    def __init__(self, run: dict) -> None:
        self.run = run
        self.lease_id: str | None = None
        self.skills_attested = False
        self.lease_ready = False
        self.lease_lock = asyncio.Lock()
        self.lease_task: asyncio.Task | None = None  # a sandbox taken ahead of the first tool that needs it
        self.lease_used = False  # a tool asked for the sandbox (an early one may end the turn unused)
        self.artifacts: list[dict] = []
        self.delivered: dict[str, dict] = {}  # sandbox path -> confirmed content fingerprint
        self.previews: dict[str, dict] = {}  # filename -> latest preview manifest
        self.preview_task: asyncio.Task | None = None
        self.view_warm_task: asyncio.Task | None = None
        self.view_warmed_at = float("-inf")
        self.preview_again = False
        self.disabled_skills: frozenset[str] = frozenset()  # the user switched these off
        self.personal: dict[str, dict] = {}  # the user's own skills (files in their library), by name
        self.skill_files: dict[str, dict[str, bytes]] = {}  # personal skills read this turn: name -> their files
        self.thinking: list[str] = []  # the model's reasoning across the turn's steps, kept for the conversation view
        self.thinking_seconds = 0.0
        self.messages: list[dict] = []  # the conversation as the model sees it: where fetchable URLs come from
        self.net_budget = net_tools.Budget()
        self.allowed_tools: set[str] | None = None  # None until the turn's tools are known
        self.offered: list[dict] = []  # the tool schemas of the latest request; subtasks reuse them to share its prefix


class _DeltaWriter:
    """Batches streamed text into events (one per ~quarter second, not one per token): agent.delta for the answer,
    agent.thinking for the model's reasoning (with how long it has been thinking)."""

    def __init__(self, loop: AgentLoop, run_id: str, step: int, kind: str = "agent.delta") -> None:
        self.loop, self.run_id, self.step, self.kind = loop, run_id, step, kind
        self.buffer: list[str] = []
        self.buffer_chars = 0
        self.lock = asyncio.Lock()
        self.last = time.monotonic()
        self.started: float | None = None
        self.seconds = 0.0

    async def __call__(self, text: str) -> None:
        if self.started is None:
            self.started = time.monotonic()
        self.buffer.append(text)
        self.buffer_chars += len(text)
        if time.monotonic() - self.last >= DELTA_FLUSH_SECONDS or self.buffer_chars >= DELTA_FLUSH_CHARS:
            await self.flush()

    async def flush(self) -> None:
        async with self.lock:
            if not self.buffer:
                return
            text, self.buffer = "".join(self.buffer), []
            self.buffer_chars = 0
            self.last = time.monotonic()
            payload = {"step": self.step, "text": text}
            if self.kind == "agent.thinking":
                payload["seconds"] = self.seconds = round(time.monotonic() - (self.started or time.monotonic()), 1)
            await self.loop._event(self.run_id, self.kind, payload)


async def _stream_with_deltas[T](awaitable: Awaitable[T], *writers: _DeltaWriter) -> T:
    """Flush independently of model tokens: tool arguments can take seconds after the last visible text."""

    async def tick() -> None:
        while True:
            await asyncio.sleep(DELTA_FLUSH_SECONDS)
            for writer in writers:
                await writer.flush()

    producer = asyncio.ensure_future(awaitable)
    flusher = asyncio.create_task(tick())
    try:
        done, _ = await asyncio.wait({producer, flusher}, return_when=asyncio.FIRST_COMPLETED)
        if flusher in done:
            flusher.result()  # propagate a stale-attempt rejection and stop the upstream
        return await producer
    finally:
        for task in (producer, flusher):
            if not task.done():
                task.cancel()
        await asyncio.gather(producer, flusher, return_exceptions=True)
        for writer in writers:
            await writer.flush()


def _arguments_json(arguments: dict) -> str:
    """Arguments as sent back to the model: the original text if it was not valid JSON."""
    if INVALID_ARGUMENTS_KEY in arguments:
        return str(arguments[INVALID_ARGUMENTS_KEY] or "{}")
    return json.dumps(arguments, ensure_ascii=False)


def _token_limit_error(run: dict) -> str:
    return "本轮累计用量已达上限；请在新任务中继续"


def _clip_arguments(arguments: dict, limit: int = 500) -> dict:
    """Keep run_events small: write_file content can be tens of KB."""
    return {k: (v[:limit] + f"...[+{len(v) - limit} chars]" if isinstance(v, str) and len(v) > limit else v) for k, v in arguments.items()}


TITLE_TIMEOUT_SECONDS = 10


class TokenLimit(Exception):
    pass


class AgentLoop:
    def __init__(
        self,
        platform: AsyncPlatformClient | None = None,
        broker: AsyncBrokerClient | None = None,
        llm: LLMClient | None = None,
    ):
        self.platform = platform or AsyncPlatformClient()
        self.broker = broker or AsyncBrokerClient()
        self._states: dict[str, dict] = {}
        self._turns: dict[str, _Turn] = {}
        self._steps: dict[str, int] = {}
        self._lease_errors: dict[str, Exception] = {}
        self._steer_pending: set[str] = set()
        self._budgets: dict[str, WorkBudget] = {}
        self._module_views: dict[str, ModuleVisibility] = {}
        self.llm = llm  # tests inject one; otherwise each run uses its catalog model's client

    def _context(self, run_id):
        return ContextManager(self, run_id, catalog=get_catalog, tools_for_run=_tools_for_run, prompt_for=system_prompt_for)

    async def _event(self, run_id: str, type_: str, payload: dict, *, advance_boundary=True) -> None:
        if type_ == "agent.context_boundary":
            await self._context(run_id).activate_boundary(payload, advance=advance_boundary)
            return
        try:
            await self.platform.append_event(run_id, type_, payload)
            if budget := self._budgets.get(run_id):
                budget.progress()
        except ClientError as exc:
            if exc.status == 409:
                raise
            log.warning("event append failed run=%s type=%s", run_id, type_, exc_info=True)
        except Exception:
            log.warning("event append failed run=%s type=%s", run_id, type_, exc_info=True)

    async def _is_canceled(self, run_id: str) -> bool:
        """True if the user canceled. A failed check counts as "not canceled": one API
        hiccup must not fail a run whose task is fine."""
        try:
            # the light /state endpoint, not the full run with artifacts and usage: this runs every couple of seconds
            heartbeat = await self.platform.get_run_state(run_id)
            if heartbeat.get("steer_pending", False):
                self._steer_pending.add(run_id)
            else:
                self._steer_pending.discard(run_id)
            return heartbeat["status"] == RunStatus.CANCELED
        except ClientError as exc:
            if exc.status == 409:
                raise
            log.warning("cancel check failed for %s; assuming not canceled", run_id, exc_info=True)
            return False
        except Exception:
            log.warning("cancel check failed for %s; assuming not canceled", run_id, exc_info=True)
            return False

    async def _interruptible(self, run_id: str, awaitable: Awaitable[T], *, steerable: bool = False) -> T:
        """Await a model call or tool, abandoning it if the user cancels the run.

        The sandbox command itself keeps running until the lease is released, which
        the caller does right after on RunCanceled. A steerable wait also ends with
        SteerArrived once a user message is pending, so the model reads it within one
        poll instead of after a wait of up to five minutes.
        """
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                if run_id in self._lease_errors:
                    raise LeaseInterrupted() from self._lease_errors[run_id]
                done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
                if done:
                    result = task.result()
                    if budget := self._budgets.get(run_id):
                        budget.progress()
                    return result
                budget = self._budgets.get(run_id)
                if budget and not budget.suspended and time.monotonic() - budget.last_progress_at >= settings.run_stall_seconds:
                    raise BudgetPause("长时间未收到模型或工具进展，已暂停，可继续")
                if await self._is_canceled(run_id):
                    raise RunCanceled()
                if steerable and run_id in self._steer_pending:
                    raise SteerArrived()
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _acquire_lease(self, run_id: str, *, wait: bool = True) -> dict:
        # A lease taken ahead of need runs beside the model call, whose time still counts: only a real wait pauses the budget.
        budget = self._budgets.get(run_id) if wait else None
        if budget:
            budget.update()
            budget.suspended = True
        try:
            return await self._wait_lease(run_id, wait=wait)
        finally:
            if budget:
                budget.update()
                budget.suspended = False
                budget.progress()

    async def _wait_lease(self, run_id: str, *, wait: bool = True) -> dict:
        """Take a sandbox, waiting in line (like the design's waiting_sandbox) when at capacity."""
        waited = 0.0
        announced = False
        while True:
            try:
                if await self._is_canceled(run_id):
                    raise RunCanceled()
                return await self._interruptible(run_id, self.broker.create_lease(run_id))
            except ClientError as exc:
                if exc.status != 429:
                    raise
            if not wait:
                raise ToolError("no sandbox is free right now")
            if waited >= settings.sandbox_wait_seconds:
                raise ToolError(f"no sandbox became available within {settings.sandbox_wait_seconds}s")
            if not announced:
                announced = True
                await self._event(run_id, "agent.waiting_sandbox", {"reason": "sandbox capacity reached"})
            if await self._is_canceled(run_id):
                raise RunCanceled()
            await asyncio.sleep(SANDBOX_RETRY_SECONDS)
            waited += SANDBOX_RETRY_SECONDS

    async def _deliver_leftovers(self, run_id: str, turn: _Turn) -> None:
        """Archive each changed revision; persist its intent before a possibly successful remote commit."""
        state = self._states[run_id]
        intents = state.setdefault("delivery_intents", {})
        state.pop("delivery_incomplete", None)
        # An API success followed by process death may have no local confirmation yet.
        for path, intent in list(intents.items()):
            result = await self.platform.delivery_receipt(run_id, intent["id"])
            if result:
                turn.delivered[path] = {"sha256": result["sha256"], "size": result["size"]}
                if not any(a.get("file_id") == result["file_id"] for a in turn.artifacts):
                    turn.artifacts.append({**result, "auto": True})
                del intents[path]
                await self._checkpoint(run_id, turn)
        try:
            files = await self.broker.output_manifest(turn.lease_id)
        except Exception as exc:
            if isinstance(exc, ClientError) and exc.status == 409:
                raise
            log.warning("could not fingerprint leftovers for %s", run_id, exc_info=True)
            state["delivery_incomplete"] = {"reason": "manifest_unavailable"}
            await self._event(run_id, "agent.warning", {"message": "交付区未能完整核对，请检查未交付文件"})
            return
        pending = [
            f
            for f in files
            if f["path"].startswith(OUTPUTS + "/") and turn.delivered.get(f["path"]) != {"sha256": f["sha256"], "size": f["size"]}
        ]
        for item in pending[: settings.max_auto_deliver_files]:
            path = item["path"]
            intent = intents.get(path)
            if not intent or (intent["sha256"], intent["size"]) != (item["sha256"], item["size"]):
                intent = intents[path] = {"id": f"{run_id}:auto:{uuid4().hex}", "sha256": item["sha256"], "size": item["size"]}
                await self._checkpoint(run_id, turn)
            try:
                identity = delivery_id_var.set(intent["id"])
                try:
                    result = await self.broker.commit(turn.lease_id, path, expected_sha256=intent["sha256"])
                finally:
                    delivery_id_var.reset(identity)
            except Exception as exc:
                if isinstance(exc, ClientError) and exc.status == 409:
                    raise
                log.warning("auto-deliver failed run=%s", run_id, exc_info=True)
                state["delivery_incomplete"] = {"reason": "commit_failed"}
                await self._event(run_id, "agent.warning", {"message": "有文件未能自动交付"})
                continue
            turn.delivered[path] = {"sha256": result["sha256"], "size": result["size"]}
            turn.artifacts.append({**result, "auto": True})
            del intents[path]
            await self._checkpoint(run_id, turn)
            await self._event(run_id, "agent.autodeliver", {"path": path, "filename": result.get("filename")})
        if len(pending) > settings.max_auto_deliver_files:
            state["delivery_incomplete"] = {"reason": "file_limit", "count": len(pending) - settings.max_auto_deliver_files}
            await self._event(run_id, "agent.warning", {"message": f"还有 {len(pending) - settings.max_auto_deliver_files} 个文件未交付"})

    async def _ensure_lease(self, turn: _Turn, run_id: str, *, required: bool = False, early: bool = False) -> str:
        """The turn's sandbox. ``early``: taken ahead of need, never queued, and not yet used by any tool."""
        turn.lease_used = turn.lease_used or not early
        async with turn.lease_lock:  # a lease taken ahead of need may still be starting when the first tool asks
            return await self._ensure_lease_locked(turn, run_id, required=required, wait=not early)

    async def _ensure_lease_locked(self, turn: _Turn, run_id: str, *, required: bool, wait: bool) -> str:
        if turn.lease_id and turn.lease_ready:
            loaded = self._states[run_id].get("loaded_skills", [])
            if not turn.skills_attested and any(item.get("kind") in {"base", "builtin"} for item in loaded):
                await verify_sandbox_skills(self.broker, turn.lease_id)
                turn.skills_attested = True
            return turn.lease_id
        lease = await self._acquire_lease(run_id, wait=wait) if not turn.lease_id else {"lease_id": turn.lease_id}
        turn.lease_id = lease["lease_id"]
        await self._event(run_id, "agent.sandbox", {"lease_id": turn.lease_id, "provider": lease.get("provider")})
        if lease.get("restored") is False and lease.get("restore_error"):
            log.warning("workspace restore failed for %s: %s", run_id, lease["restore_error"])
            if required:  # a checkpoint that counts on these files must not continue without them
                raise LeaseInterrupted("工作区恢复失败")
            await self._event(run_id, "agent.warning", {"message": "上一轮的工作区没能恢复"})
        loaded = self._states[run_id].get("loaded_skills", [])
        if any(item.get("kind") in {"base", "builtin"} for item in loaded):
            await verify_sandbox_skills(self.broker, turn.lease_id)
            turn.skills_attested = True
        existing = set(turn.skill_files)
        for item in loaded:
            if item.get("kind") == "personal" and item["name"] not in turn.skill_files:
                current = turn.personal.get(item["name"])
                try:
                    data = await self.platform.get_file_bytes(item["file_id"], run_id=run_id, job="skill", max_bytes=skillfiles.MAX_ZIP)
                    filename = item.get("filename")
                    if not filename and current and current.get("file_id") == item["file_id"]:
                        filename = current.get("filename")
                    if not filename:
                        filename = "skill.zip" if data.startswith(b"PK\x03\x04") else "SKILL.md"
                    _, files = skillfiles.unpack(data, filename)
                    turn.skill_files[item["name"]] = files
                    await self._install_skill(turn, item["name"])
                except Exception:
                    title = item.get("title") or (current or {}).get("title") or item["name"]
                    await self._event(run_id, "agent.warning", {"message": f"技能「{title}」已不可用，本轮不再使用它的附带文件"})
        for name in existing:  # personal skills read before the sandbox existed
            await self._install_skill(turn, name)
        # A saved confirmation describes delivered bytes. Rehashing current restored files here would
        # incorrectly confirm edits made after the last delivery, especially during checkpoint recovery.
        for path, fingerprint in (turn.run.get("workspace_delivered") or {}).items():
            if isinstance(fingerprint, dict):
                turn.delivered.setdefault(path, fingerprint)
        turn.lease_ready = True
        self._warm_view(turn, run_id)
        return turn.lease_id

    def _views_office_work(self, turn: _Turn, run_id: str) -> bool:
        """The run can view its work (vision model, Work mode) and has an Office skill, loaded now or carried in its history."""
        return "view_workspace_file" in (turn.allowed_tools or ()) and any(
            item["name"] in _OFFICE_VIEW_SKILLS for item in self._states[run_id].get("loaded_skills", [])
        )

    def _prepare_office_turn(self, turn: _Turn, run_id: str) -> None:
        """Every turn gets a fresh sandbox, so a follow-up edit would view through a cold LibreOffice (11+ s).

        A turn that continues an Office workspace takes its sandbox and starts the listener during the first model call.
        It never queues for a sandbox; on any failure the first tool takes the lease as usual.
        """
        if turn.lease_id or not turn.run.get("workspace_restore_key") or not self._views_office_work(turn, run_id):
            return
        turn.lease_task = asyncio.create_task(self._prepare_lease(turn, run_id))

    async def _prepare_lease(self, turn: _Turn, run_id: str) -> None:
        try:
            await self._ensure_lease(turn, run_id, early=True)
        except Exception:
            log.info("early sandbox for run=%s not taken; the first tool takes it", run_id, exc_info=True)

    def _warm_view(self, turn: _Turn, run_id: str) -> None:
        """Start the sandbox's LibreOffice listener while the model is still writing an Office file.

        Repeated sandbox activity keeps the listener inside its idle window, at most one call a minute.
        """
        if not turn.lease_ready or not turn.lease_id or not self._views_office_work(turn, run_id):
            return
        if (turn.view_warm_task and not turn.view_warm_task.done()) or time.monotonic() - turn.view_warmed_at < 60:
            return
        turn.view_warmed_at = time.monotonic()
        turn.view_warm_task = asyncio.create_task(self._warm_view_once(run_id, turn.lease_id))

    async def _warm_view_once(self, run_id: str, lease_id: str) -> None:
        try:
            await self.broker.warm_workspace_view(lease_id)
        except Exception:
            log.info("workspace view warm-up failed run=%s; the first view starts the listener itself", run_id)

    # ---- personal skills: read from the user's library, their files copied into the sandbox
    async def _read_personal(self, turn: _Turn, run_id: str, name: str) -> str:
        skill = turn.personal[name]
        data = await self.platform.get_file_bytes(skill["file_id"], run_id=run_id)
        try:
            text, files = skillfiles.unpack(data, skill["filename"])
        except (skillfiles.SkillFileError, KeyError, ValueError) as exc:
            raise ToolError(f"个人技能 {name} 的文件读不出来：{exc}") from exc
        turn.skill_files[name] = files
        if turn.lease_id:
            await self._install_skill(turn, name)
        return personal_header(name) + text

    async def _install_skill(self, turn: _Turn, name: str) -> None:
        for rel, data in turn.skill_files[name].items():
            await self.broker.put_file(turn.lease_id, f"/workspace/.ailab/skills/{name}/{rel}", data)

    # ---- live previews: rendered by the Broker in the background while the model keeps working
    def _schedule_previews(self, turn: _Turn, run_id: str) -> None:
        if not turn.lease_id:
            return
        if turn.preview_task and not turn.preview_task.done():
            turn.preview_again = True  # the running render may have missed this change
            return
        turn.preview_task = asyncio.create_task(self._render_previews(turn, run_id))

    async def _render_previews(self, turn: _Turn, run_id: str) -> None:
        while True:
            turn.preview_again = False
            try:
                manifests = await self.broker.refresh_previews(turn.lease_id)
            except Exception:
                log.warning("preview refresh failed for %s", run_id, exc_info=True)
                await self._event(run_id, "preview.failed", {"error": "预览生成失败"})
                return
            for m in manifests:
                if m.get("error"):
                    log.warning("preview failed for %s: %s", run_id, m["error"])
                    await self._event(run_id, "preview.failed", {**m, "error": "预览生成失败"})
                else:
                    turn.previews[m["filename"]] = m
                    await self._event(run_id, "preview.updated", m)
            if not turn.preview_again:
                return

    async def _finish_previews(self, turn: _Turn, run_id: str) -> None:
        if turn.preview_task:
            await asyncio.gather(turn.preview_task, return_exceptions=True)
        if turn.lease_id:
            await self._render_previews(turn, run_id)  # the last change may have come after the last render

    async def _snapshot(self, turn: _Turn, run_id: str) -> dict | None:
        """The sandbox's workspace archive, or None when it could not be taken. A failure is visible as a warning
        and is never reported as saved; the next resume is told the snapshot lags the checkpoint."""
        try:
            return await self.broker.snapshot(turn.lease_id)
        except Exception:
            log.warning("workspace snapshot failed for %s", run_id, exc_info=True)
            await self._event(run_id, "agent.warning", {"message": "工作区没能保存，下一轮需要重新生成文件"})
            return None

    async def _call_model(
        self,
        llm: LLMClient,
        run_id: str,
        run: dict,
        model: str,
        messages: list[dict],
        step: int,
        tools=None,
        *,
        quiet=False,
        max_output_tokens=None,
        tool_choice=None,
        allow_fallback=True,
        continues_prefix=False,
    ):
        offered = tools_for_turn() if tools is None else tools
        catalog = get_catalog()
        if self.llm is not None and catalog.get(model) is None:
            catalog = Catalog([ModelSpec(model, model, provider="mock")])
        policy = ModelPolicy(catalog)
        required = {"tools"} if offered else set()
        if has_image_part(messages):
            required.add("vision")
        candidates = policy.candidates(model, required)
        if not allow_fallback:
            candidates = candidates[:1]
        announced_model = None
        for index, spec in enumerate(candidates):
            if not quiet and run.get("_last_model") and run["_last_model"] != spec.id and announced_model != spec.id:
                await self._event(run_id, "agent.context_boundary", {"reason": "model_changed"})
                announced_model = spec.id
            if request_tokens(self._states.get(run_id, {}), spec.id, messages, offered) + min(
                128, spec.context_tokens // 100
            ) > policy.input_budget(spec):
                raise LLMError(f"context budget exceeded for {spec.id}")
            client = llm if spec.id == model else llm_for(spec.id)
            for retry in range(settings.llm_max_retries + 1):
                try:
                    return await self._model_attempt(
                        client,
                        run_id,
                        run,
                        spec.id,
                        messages,
                        step,
                        offered,
                        quiet=quiet,
                        max_output_tokens=max_output_tokens,
                        tool_choice=tool_choice,
                        continues_prefix=continues_prefix,
                    )
                except LLMError as exc:
                    if (
                        quiet
                        and step in (-1, -3)
                        and (not policy.retryable(exc) or retry == settings.llm_max_retries or getattr(exc, "output_started", False))
                    ):
                        metrics.CONTEXT_COMPACTION_FAILURES.labels("model_error").inc()
                    if getattr(exc, "output_started", False) or not policy.retryable(exc):
                        raise
                    if index + 1 < len(candidates):
                        await self._event(
                            run_id,
                            "agent.model_fallback",
                            {"from": spec.id, "to": candidates[index + 1].id, "reason": "upstream_unavailable"},
                        )
                        if not quiet:
                            await self._event(run_id, "agent.context_boundary", {"reason": "model_changed"})
                        announced_model = candidates[index + 1].id
                        break
                    if retry == settings.llm_max_retries:
                        raise
                    await asyncio.sleep(settings.llm_retry_backoff_seconds * 2**retry * random.uniform(0.5, 1.5))
        raise LLMError("all eligible models failed")

    async def _answer_without_tools(self, llm, run_id, run, model, messages, step, tools, *, quiet=False, allow_fallback=True):
        """Ask for a text answer that continues the conversation (a summary, the out-of-steps wrap-up).

        The request keeps the conversation's shape: the same tools with ``tool_choice=auto`` and the same reasoning
        setting. ``tool_choice=none`` or a reasoning toggle re-renders the provider's prompt template and loses the
        whole cached prefix (DeepSeek, 2026-11-01: 9.3k of 9.5k cached tokens fell to 1.2k). The instruction asks for
        no tool calls; a model that still makes one receives paired refusals in an appended retry.
        """
        options = {"quiet": quiet, "allow_fallback": allow_fallback, "continues_prefix": True}
        response = await self._call_model(llm, run_id, run, model, messages, step, tools=tools, **options)
        if response.tool_calls and (quiet or not response.content.strip()):
            assistant = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": _arguments_json(call.arguments)}}
                    for call in response.tool_calls
                ],
            }
            if response.reasoning_content:
                assistant["reasoning_content"] = response.reasoning_content
            refused = [
                tool_result_message(call.id, call.name, {"ok": False, "error": "本次仅返回文本，不执行工具"})
                for call in response.tool_calls
            ]
            retry = [*messages, assistant, *refused, {"role": "user", "content": "请直接返回所需文本，不调用工具。"}]
            response = await self._call_model(llm, run_id, run, model, retry, step, tools=tools, **options)
        if not (quiet and step in (-1, -3)):
            response.tool_calls = []
        return response

    async def _model_attempt(
        self,
        llm: LLMClient,
        run_id: str,
        run: dict,
        model: str,
        messages: list[dict],
        step: int,
        tools=None,
        *,
        quiet=False,
        max_output_tokens=None,
        tool_choice=None,
        continues_prefix=False,
    ):
        if messages and hasattr(self.platform, "get_memory_context") and not quiet:
            await self._context(run_id).refresh_memory(run_id, messages)
        if not quiet:
            await self._context(run_id).revalidate_recall(run_id, messages)
        if run.get("_ledger_tokens", 0) >= settings.run_max_tokens:
            raise LLMError("token limit reached before model attempt")
        model_label = metrics.model_label(model)
        call_started = time.perf_counter()
        writer = _DeltaWriter(self, run_id, step)
        thinker = _DeltaWriter(self, run_id, step, "agent.thinking")
        offered = tools_for_turn() if tools is None else tools
        estimated_input = request_tokens(self._states.get(run_id, {}), model, messages, offered)
        if not quiet:
            metrics.CONTEXT_INPUT_TOKENS.observe(estimated_input)
        spec = get_catalog().get(model)
        # A small, explicit window reserve covers unsent delta/framing error;
        # it does not inflate the 90% trigger, band accounting or usage ledger.
        safety = min(128, (spec.context_tokens if spec else 32768) // 100)
        if spec is not None and estimated_input + safety > ModelPolicy(get_catalog()).input_budget(spec):
            raise LLMError("updated memory and current input exceed context budget")
        output_limit = ModelPolicy.output_budget(spec or ModelSpec(model, model), estimated_input + safety)
        if output_limit < 1:
            raise LLMError("no output space remains in the model context window")
        if settings.model_max_output_tokens is not None:
            output_limit = min(output_limit, settings.model_max_output_tokens)
        if quiet:
            # A continuation keeps reasoning on, and reasoning shares the output budget with the summary itself.
            output_limit = min(output_limit, 8192 if continues_prefix else 2048)
        if max_output_tokens is not None:
            output_limit = min(output_limit, max_output_tokens)
        call = ModelCall(
            uuid4().hex,
            run_id,
            output_limit,
            actual_backend=model,
            quiet=quiet,
            tool_choice=tool_choice,
            continues_prefix=continues_prefix,
            session=run.get("conversation_id") or run_id,
            reasoning=(run.get("options") or {}).get("reasoning") or "",
        )
        try:
            await self.platform.reserve_usage(
                {
                    "call_id": call.call_id,
                    "run_id": run_id,
                    "attempt_id": run["_attempt_id"],
                    "alias": model,
                    "mode": (run.get("options") or {}).get("mode", "work"),
                    "quiet": quiet,
                    "max_output_tokens": call.max_output_tokens,
                    "estimated_input_tokens": estimated_input,
                }
            )
        except ClientError as exc:
            # Only the business reservation refusal pauses work. Upstream model
            # throttling and unrelated 429s keep their own failure/retry contracts.
            if (detail := exc.quota_detail) is not None:
                raise QuotaPause(detail["window"], detail["resets_at"]) from exc
            raise
        if not quiet and (gate := run.get("_main_model_reserved")) is not None:
            gate.set()
        token = model_call.set(call)

        async def on_text(text: str) -> None:
            call.emitted = True
            if quiet:
                return
            await thinker.flush()
            await writer(text)

        async def on_reasoning(text: str) -> None:
            call.emitted = True
            if not quiet:
                await thinker(text)

        async def submit() -> dict:
            usage = call.usage
            status = UsageStatus.SETTLED if usage else UsageStatus.ESTIMATED if call.reached_model else UsageStatus.RELEASED
            output = max(0, (call.output_bytes + 3) // 4)
            payload = {
                "call_id": call.call_id,
                "attempt_id": run["_attempt_id"],
                "actual_backend": call.actual_backend,
                "input_tokens": usage.get("prompt_tokens", estimated_input if call.reached_model else 0),
                "cached_tokens": usage.get("cached_tokens", 0),
                "output_tokens": usage.get("completion_tokens", output if call.reached_model else 0),
                "reasoning_tokens": usage.get("reasoning_tokens", 0),
                "status": status,
            }
            settled = await self.platform.settle_usage(payload)
            if usage:
                mode = (run.get("options") or {}).get("mode", "work")
                kind = "title" if step == -2 else "compaction" if step in {-1, -3} else "main"
                metrics.record_prompt_cache(mode if mode in {"chat", "work"} else "work", kind, usage)
            return settled

        try:
            if await self._is_canceled(run_id):
                raise RunCanceled()
            with span("model.call", model=model_label):
                stream = llm.chat_stream(
                    messages,
                    offered,
                    model,
                    user_id=run.get("user_id"),
                    on_text=on_text,
                    on_reasoning=on_reasoning,
                )
                response = await self._interruptible(
                    run_id,
                    stream if quiet else _stream_with_deltas(stream, thinker, writer),
                )
        except BaseException as exc:
            if isinstance(exc, LLMError):
                exc.output_started = exc.output_started or call.emitted or bool(call.output_bytes)
            metrics.LLM_CALLS.labels(model_label, "error").inc()
            try:
                settled = await submit()
                run["_ledger_tokens"] = settled["run_tokens"]
            except Exception as settlement_error:
                log.warning("usage settlement pending reaper call=%s", call.call_id, exc_info=True)
                raise RuntimeError("model usage settlement failed; execution stopped") from settlement_error
            raise
        else:
            if response.usage:
                call.usage = response.usage
            # Exhausted settlement retries stop execution before tools or another call.
            # The durable intent remains charged until the reaper makes the gap visible.
            settled = await submit()
            run["_ledger_tokens"] = settled["run_tokens"]
            actual = call.usage.get("prompt_tokens", 0)
            if actual > 0:
                metrics.CONTEXT_DELTA_ERROR.labels("anchor_delta").observe((estimated_input - actual) / actual)
                raw = estimator.estimate(model, {"messages": messages, "tools": offered})
                metrics.CONTEXT_DELTA_ERROR.labels("raw").observe((raw - actual) / actual)
                record_usage(self._states.setdefault(run_id, fresh_state()), model, messages, offered, actual)
            response.thinking_seconds = thinker.seconds
            if not quiet:
                run["_last_model"] = model
            metrics.LLM_CALLS.labels(model_label, "ok").inc()
            return response
        finally:
            model_call.reset(token)
            ttft_metric = getattr(metrics, "MODEL_TTFT_SECONDS", None)
            if ttft_metric is not None and call.ttft_seconds is not None:
                ttft_metric.labels(metrics.model_label(model)).observe(call.ttft_seconds)
            metrics.LLM_SECONDS.labels(model_label).observe(time.perf_counter() - call_started)

    async def _conversation_title(self, run_id: str, run: dict, answer: str = "") -> str | None:
        if not run.get("needs_conversation_title"):
            return None
        model = run.get("_last_model") or run["model"]
        prompt = [
            {
                "role": "system",
                "content": "概括用户问题生成简洁中文会话标题，不逐字照抄首句，最多40字。只输出标题文字，不加引号、Markdown或说明。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": for_history(run["prompt"], keep_ui=False, limit=max(len(run["prompt"]), 1000))[:1000],
                        "files": [f.get("filename", "")[:200] for f in (run.get("input_files") or [])][:50],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        # The main reservation owns priority, but preparation must not spend the
        # metadata inference budget. The enclosing run deadline/cancellation owns
        # this wait; also bound direct callers that do not use the job runner.
        if (gate := run.get("_main_model_reserved")) is not None:
            async with asyncio.timeout(settings.run_timeout_seconds):
                await self._interruptible(run_id, gate.wait())
        reason = "none"
        try:
            async with asyncio.timeout(TITLE_TIMEOUT_SECONDS):
                response = await self._call_model(self.llm or llm_for(model), run_id, run, model, prompt, -2, tools=[], quiet=True)
                candidate = normalize_title(response.content) or None
                if not candidate:
                    reason = "empty"
        except (RunCanceled, asyncio.CancelledError):
            raise
        except Exception as exc:
            if isinstance(exc, ClientError) and exc.status == 409:
                raise
            reason = "timeout" if isinstance(exc, TimeoutError) else "error"
            candidate = None
            log.warning("conversation title inference failed run=%s error=%s", run_id, type(exc).__name__)
        outcome = "ok"
        if not candidate:
            # A visible deterministic label is preferable to an indefinitely
            # unnamed conversation. API empty-title CAS still preserves renames.
            first_line = next((line for line in run["prompt"].splitlines() if line.strip()), "")
            candidate = normalize_title(first_line) or normalize_title(run["prompt"]) or "对话"
            outcome = "fallback"
        try:
            if hasattr(self.platform, "set_conversation_title"):
                await self.platform.set_conversation_title(run_id, candidate)
            metrics.CONVERSATION_TITLES.labels(outcome, reason).inc()
            return candidate
        except (RunCanceled, asyncio.CancelledError):
            raise
        except Exception as exc:
            # Never hide cancellation/ownership loss behind a metadata retry.
            metrics.CONVERSATION_TITLES.labels("timeout" if isinstance(exc, TimeoutError) else "error", "persist").inc()
            log.warning("conversation title persistence failed run=%s error=%s", run_id, type(exc).__name__)
            return None

    async def _revoked_recall_results(self, run_id, *args, **kwargs):
        return await self._context(run_id)._revoked_recall_results(run_id, *args, **kwargs)

    async def _refresh_boundary_memory(self, run_id, *args, **kwargs):
        return await self._context(run_id)._refresh_boundary_memory(run_id, *args, **kwargs)

    async def _assemble_context(self, run_id, *args, **kwargs):
        return await self._context(run_id)._assemble_context(run_id, *args, **kwargs)

    _compacted_recent = staticmethod(ContextManager._compacted_recent)

    _delivery_note = staticmethod(ContextManager._delivery_note)

    async def _compact_parent_context(self, run_id, *args, **kwargs):
        return await self._context(run_id)._compact_parent_context(run_id, *args, **kwargs)

    async def _watchdog(self, llm, run_id, turn, model, offered, budget) -> None:
        """Every watchdog_interval_seconds of active work, judge whether the run still advances the goal."""
        state, messages = self._states[run_id], turn.messages
        if budget.data["active_seconds"] < budget.data.setdefault("watchdog_at", settings.watchdog_interval_seconds):
            return
        budget.data["watchdog_at"] = budget.data["active_seconds"] + settings.watchdog_interval_seconds
        try:
            # Same messages and tools as the next real call, so the check is a prefix-cache hit plus one short turn.
            response = await self._call_model(
                llm,
                run_id,
                turn.run,
                model,
                [*messages, {"role": "user", "content": watchdog.PROMPT}],
                -1,
                tools=offered,
                quiet=True,
                continues_prefix=True,
            )
        except LLMError:
            log.warning("watchdog check failed run=%s", run_id, exc_info=True)
            return
        result = watchdog.verdict(None if response.tool_calls else response.content)
        metrics.WATCHDOG_VERDICTS.labels(result["verdict"]).inc()
        await self._event(run_id, "agent.watchdog", result)
        if settings.watchdog_mode == "nudge" and result["verdict"] != "unknown":
            if result["verdict"] == "progressing":
                state.pop("watchdog_nudged", None)
            elif state.get("watchdog_nudged"):
                raise BudgetPause(watchdog.PAUSE)
            else:
                state["watchdog_nudged"] = True
                reason = result["reason"] or "最近没有明显进展"
                messages.append({"role": "user", "content": watchdog.NUDGE.format(reason=reason)})
                await self._event(run_id, "agent.budget", {"kind": "nudge", "reason": f"监督检查：{reason}"})
        await self._checkpoint(run_id, turn)

    async def _subtasks(self, turn: _Turn, run_id: str, call, step: int) -> dict:
        try:
            tasks = subtasks.validate(call.arguments)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        messages = turn.messages
        # Fork at the parent's latest request: everything before the assistant message that asked for the subtasks.
        cut = next(
            (
                i
                for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") == "assistant" and any(c.get("id") == call.id for c in messages[i].get("tool_calls") or [])
            ),
            len(messages),
        )
        base = [dict(m) for m in messages[:cut]]
        lease_id = await self._ensure_lease(turn, run_id)
        llm = self.llm or llm_for(turn.run.get("model") or "")
        children = [
            asyncio.create_task(self._subtask(turn, run_id, llm, base, lease_id, index, task, step)) for index, task in enumerate(tasks)
        ]
        try:
            results = await asyncio.gather(*children)
        except BaseException:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            raise
        return {"ok": all(r["status"] == "done" for r in results), "results": results}

    async def _subtask(self, turn: _Turn, run_id: str, llm, base: list[dict], lease_id: str, index: int, task: dict, step: int) -> dict:
        run, model, offered = turn.run, turn.run.get("model") or "", turn.offered
        messages = [*base, {"role": "user", "content": subtasks.BRIEF.format(**task)}]
        await self._event(run_id, "agent.subtask", {"index": index, "title": task["title"], "status": "started"})
        report, status, used = "", "incomplete", 0
        for used in range(1, settings.subtask_max_steps + 1):
            try:
                # A fork of the parent's request: same tools and reasoning setting, so the shared prefix stays cached.
                response = await self._call_model(llm, run_id, run, model, messages, step, tools=offered, quiet=True, continues_prefix=True)
            except LLMError as exc:
                report, status = f"模型调用失败：{exc}"[:300], "failed"
                break
            assistant = {"role": "assistant", "content": response.content or ""}
            if response.tool_calls:
                assistant["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": _arguments_json(c.arguments)}}
                    for c in response.tool_calls
                ]
            if response.reasoning_content:
                assistant["reasoning_content"] = response.reasoning_content
            messages.append(assistant)
            report = response.content or report
            if not response.tool_calls:
                status = "done"
                break
            child_images = []
            for ordinal, c in enumerate(response.tool_calls):
                version = catalog_version(run.get("options"))
                available = c.name in subtasks.allowed(version)
                if version == 2:
                    available = available and c.name in {t["function"]["name"] for t in offered}
                if available:
                    # The parent step is part of the invocation: the same command in the same slot of a
                    # second run_subtasks is a different execution, not a retry of the first one.
                    result = await self._subtask_tool(turn, run_id, lease_id, c, messages, f"step{step}:sub{index}:{used}:{ordinal}")
                else:
                    result = {"ok": False, "error": subtasks.REFUSED.format(name=c.name)}
                child_images.extend(result.pop("_child_images", []))
                messages.append(tool_result_message(c.id, c.name, result))
            # Complete every tool/result pair before appending visual inputs.
            messages.extend({"role": "user", "content": parts} for parts in child_images)
        if status == "incomplete":
            report = subtasks.OUT_OF_STEPS + "\n" + report
        await self._event(run_id, "agent.subtask", {"index": index, "title": task["title"], "status": status, "steps": used})
        return {"title": task["title"], "status": status, "steps": used, "report": report[: subtasks.REPORT_CHARS]}

    async def _subtask_tool(self, turn: _Turn, run_id: str, lease_id: str, call, messages: list[dict], invocation: str = "") -> dict:
        ctx = ToolContext(
            run_id,
            self.platform,
            messages,
            turn.net_budget,
            mode=(turn.run.get("options") or {}).get("mode", "work"),
            document_tools_version=catalog_version(turn.run.get("options")),
        )
        budget = self._budgets[run_id]
        budget.update()
        ctx.exec_lifetime_seconds = max(1, settings.run_max_seconds - budget.data["active_seconds"])
        if call.name == "bash":
            ctx.command_handle = command_handle(run_id, invocation, command_text(call.arguments))
        try:
            if call.name == "view_workspace_file" and ctx.document_tools_version == 2:
                import base64

                from .checkpoint_budget import encoded_size
                from .multimodal import image_part

                spec = get_catalog().get(turn.run.get("model"))
                if not spec or not spec.supports({"vision"}):
                    raise ToolError("当前模型不支持图像理解")
                if set(call.arguments) - {"path", "pages"} or not isinstance(call.arguments.get("path"), str):
                    raise ToolError("请提供工作区文件路径和最多四个页码")
                viewed = await self._interruptible(
                    run_id, self.broker.view_workspace_file(lease_id, call.arguments["path"], call.arguments.get("pages"))
                )
                if len(viewed["images"]) > 4 or len(viewed["images"]) != len(viewed["pages"]):
                    raise ToolError("invalid workspace view result")
                additional = sum(len(item) for item in viewed["images"])
                if encoded_size({"messages": messages}) + additional + 128_000 > settings.checkpoint_max_bytes:
                    raise ToolError("子任务上下文空间不足，请减少查看页数")
                parts = [
                    [
                        {"type": "text", "text": f"工作文件 {call.arguments['path']} 第 {page} 页"},
                        image_part(base64.b64decode(encoded, validate=True), "image/jpeg"),
                    ]
                    for page, encoded in zip(viewed["pages"], viewed["images"], strict=True)
                ]
                return {
                    "ok": True,
                    "pages": viewed["pages"],
                    "input": "image",
                    **({"sheet_pages": viewed["sheet_pages"]} if viewed.get("sheet_pages") else {}),
                    "_child_images": parts,
                }
            return await self._interruptible(run_id, execute_tool(call.name, call.arguments, lease_id, self.broker, ctx))
        except (RunCanceled, LeaseInterrupted, BudgetPause):
            raise
        except ClientError as exc:
            if exc.status == 410:
                raise LeaseInterrupted() from exc
            return {"ok": False, "error": "工具请求失败"}
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            log.exception("subtask tool %s failed", call.name)
            return {"ok": False, "error": "工具执行失败"}

    async def _compact_work_context(self, run_id, *args, **kwargs):
        return await self._context(run_id).compact(run_id, *args, **kwargs)

    async def _user_content(self, run_id, run, model):
        from ..contracts.document_view import text_block
        from ..contracts.schedules import scheduled_prompt

        prompt = scheduled_prompt(run["prompt"], (run.get("options") or {}).get("schedule")) + text_block(run.get("doc_refs"))
        prompt_options = (
            {"mode": (run.get("options") or {}).get("mode", "work"), "document_tools_version": 2}
            if catalog_version(run.get("options")) == 2
            else {}
        )
        files = run.get("input_files") or []
        if prompt_options.get("mode") == "work" and run.get("workspace_restore_key"):
            injected = "本轮inputs为空" if not files else "本轮inputs仅含本消息附件清单中的原件"
            prompt += (
                "\n\n平台工作区恢复事实：已保存的work/assets/outputs可恢复，但历史inputs不会由快照恢复；"
                + injected
                + "。旧脚本若读取历史inputs，先按历史file_id用copy_library_file恢复到脚本原路径，再运行脚本，"
                "不要先执行并等待FileNotFoundError后再补文件。"
            )
        images = [
            file
            for file in files
            if (file.get("content_type") or "").startswith("image/")
            and not (prompt_options.get("document_tools_version") == 2 and file.get("input_strategy") == "reference")
        ]
        document_pages = [page for file in files for page in file.get("visual_pages", [])]
        if prompt_options.get("document_tools_version") == 2:
            # Inline pages are canonical renders: their digest is the region revision.
            document_pages = [
                {**page, "region_ref": {"page": page["page"], "revision": page["digest"]}} if page.get("digest") else page
                for page in document_pages
            ]
        if not images and not document_pages:
            return user_prompt(render_skill_mentions(prompt, (run.get("options") or {}).get("skills")), files, **prompt_options)
        parts = [
            {
                "type": "text",
                "text": user_prompt(
                    render_skill_mentions(prompt, (run.get("options") or {}).get("skills")),
                    files if prompt_options.get("document_tools_version") == 2 else [file for file in files if file not in images],
                    **prompt_options,
                ),
            }
        ]
        spec = get_catalog().get(model)
        for file in [*images, *document_pages]:
            image_parts = await self._image_content(run_id, run, file, spec)
            if (file in document_pages or prompt_options.get("document_tools_version") == 2) and not any(
                part.get("type") == "image_url" for part in image_parts
            ):
                raise ToolError("附件视觉页面不可读取，请重新准备；本轮未向模型发送不完整文档")
            parts.extend(image_parts)
        from ..memory_recall.guard import escape_memory_tags

        return escape_memory_tags(parts)

    async def _image_content(self, run_id, run, file, spec):
        parts = (
            await input_content(self.platform, run_id, file, spec, text=file.get("text", ""))
            if spec is not None
            else attachment_content(file, vision=False, text=file.get("text", ""))
        )
        for part in parts:
            if part.get("type") == "image_url":
                run.setdefault("_image_files", {})[part["image_url"]["url"]] = file
        return parts

    async def _restore_images(self, run_id, run, state):
        return await self._context(run_id)._restore_images(run_id, run, state)

    async def _ensure_skill(self, turn, run_id, name, reason, file_id=None):
        name = resolve_name(name)
        state = self._states[run_id]
        attempted = state.setdefault("attempted_skills", [])
        if name in attempted or (name not in BASE_SKILLS and _full_skill_visible(turn, name)):
            return
        if name in BASE_SKILLS:
            mode = (turn.run.get("options") or {}).get("mode", "work")
            visible = self._module_views.setdefault(run_id, ModuleVisibility()).read(state["messages"])
            if module_identity(read_module(name, mode, document_tools_version=catalog_version(turn.run.get("options")))) in visible:
                return
        call_id = "skill_" + uuid4().hex
        call = ToolCall(call_id, "load_skill", {"name": name})
        call.skill_reason = reason
        call.expected_file_id = file_id
        self._context(run_id).append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": call_id, "type": "function", "function": {"name": "load_skill", "arguments": _arguments_json(call.arguments)}}
                ],
            }
        )
        result = await self._run_tool(turn, run_id, call, self._steps[run_id])
        self._context(run_id).append(tool_result_message(call_id, "load_skill", result))
        attempted.append(name)
        await self._checkpoint(run_id, turn)

    def _register_carried_skills(self, turn, run_id) -> None:
        # Each turn is a new run: a skill loaded earlier in the conversation is loaded for this run too. Without this it
        # was sent again every turn, and nothing that depends on the loaded skills (attestation, view warm-up) saw it.
        mode = (turn.run.get("options") or {}).get("mode", "work")
        state = self._states[run_id]
        versions = {name: spec["version"] for name, spec in skills().items()}
        loaded = state.setdefault("loaded_skills", [])
        for name in sorted(_carried_skills(state.get("messages") or [], versions, mode)):
            if not any(item["name"] == name for item in loaded):
                loaded.append({"name": name, "kind": "base" if name in BASE_SKILLS else "builtin", "version": versions[name]})
        visible = self._module_views.setdefault(run_id, ModuleVisibility()).read(state.get("messages") or [])
        for result in visible.values():
            entry = next((item for item in loaded if item["name"] == result["name"]), None)
            if entry is not None and result.get("package_hash"):
                entry["package_hash"] = result["package_hash"]
                if result.get("guide_revision"):
                    entry["guide_revision"] = result["guide_revision"]
        files = {name: skill["file_id"] for name, skill in turn.personal.items() if name not in turn.disabled_skills}
        for name in sorted(_carried_skills(state.get("messages") or [], files, mode, identity="file_id")):
            if any(item["name"] == name for item in loaded):
                continue
            # The text stays in history; leasing a sandbox fetches this version's files through the run-bound read and
            # installs them, warning instead of failing if the file is gone. Chat has no sandbox and needs no read.
            skill = turn.personal[name]
            loaded.append(
                {
                    "name": name,
                    "kind": "personal",
                    "file_id": skill["file_id"],
                    "filename": skill["filename"],
                    "title": skill.get("title") or name,
                }
            )

    async def _run_tool(self, turn: _Turn, run_id: str, call, step: int, invocation: str = "", event_name: str | None = None) -> dict:
        image_start = len(self._states[run_id].get("pending_image_inputs", []))
        shown_name = event_name or call.name
        await self._event(run_id, "agent.tool_call", {"step": step, "name": shown_name, "arguments": _clip_arguments(call.arguments)})
        tool = metrics.tool_label(call.name)
        tool_started = time.perf_counter()
        wanted = None
        try:
            current_catalog = _tools_for_run(turn.run)
            if call.name not in {t["function"]["name"] for t in current_catalog} | {"search_capabilities", "finish"}:
                raise ToolError("tool is not available (disabled or unavailable)")
            if call.name == "ui_components":
                spec = get_catalog().get(turn.run.get("model"))
                if not settings.genui_enabled or not spec or not spec.supports({"genui", "tools"}):
                    raise ToolError("ui_components is not available for this model")
            if not permitted(
                call.name, mode=(turn.run.get("options") or {}).get("mode", "work"), version=catalog_version(turn.run.get("options"))
            ):
                raise ToolError("document tool is not available for this run mode/catalog")
            if turn.allowed_tools is not None and call.name not in turn.allowed_tools:
                raise ToolError("tool is not available in the current model catalog")
            if (turn.run.get("options") or {}).get("mode") == "chat" and call.name == "save_skill" and call.arguments.get("path"):
                raise ToolError("对话模式保存技能请传 content；压缩包/脚本修改请切换工作模式")
            wanted = resolve_name(call.arguments.get("name")) if call.name == "load_skill" and call.arguments.get("name") else None
            if (wanted and not wanted.startswith("platform.")) or call.name == "search_capabilities":
                latest = await self.platform.get_run(run_id)
                turn.disabled_skills = frozenset(latest.get("disabled_skills") or [])
                turn.personal = {s["name"]: s for s in latest.get("personal_skills") or []}
            if wanted and not wanted.startswith("platform.") and wanted in turn.disabled_skills:
                raise ToolError(f"技能 {wanted} 已被用户关闭，不能使用；用沙箱里的 Python 库自己完成")
            if call.name in {"view_attachment", "extract_text"}:
                from .stuck import fingerprint

                state = self._states[run_id]
                key = fingerprint({"name": call.name, "arguments": call.arguments})
                waits = state.setdefault("file_wait_deadlines", {})
                deadline = waits.setdefault(key, time.time() + (settings.ocr_deadline_seconds if call.name == "extract_text" else 150))
                if time.time() >= deadline:
                    raise ToolError("文件处理超过截止时间，已停止等待；不要sleep或重复提取，请告知用户处理失败")
            if call.name == "load_skill" and "cursor" in call.arguments:
                raise ToolError("load_skill reads whole files; cursor is unsupported")
            if wanted in BASE_SKILLS:
                mode = (turn.run.get("options") or {}).get("mode", "work")
                installed = next((item for item in self._states[run_id].get("loaded_skills", []) if item["name"] == wanted), {})
                if installed.get("package_hash", skills()[wanted]["sha256"]) != skills()[wanted]["sha256"]:
                    raise ToolError("installed skill package changed; rebuild context explicitly")
                if installed.get("version", skills()[wanted]["version"]) != skills()[wanted]["version"]:
                    raise ToolError("installed skill version is unavailable; rebuild context explicitly")
                if call.arguments.get("version") not in (None, skills()[wanted]["version"]):
                    raise ToolError("requested skill version is unavailable; rebuild context explicitly")
                try:
                    result = read_module(
                        wanted, mode, call.arguments.get("resource"), document_tools_version=catalog_version(turn.run.get("options"))
                    )
                except (ValueError, OSError) as exc:
                    raise ToolError(str(exc)) from exc
                if installed.get("guide_revision", result.get("guide_revision")) != result.get("guide_revision"):
                    raise ToolError("installed document guide changed; rebuild context explicitly")
                if module_identity(result) in self._module_views.setdefault(run_id, ModuleVisibility()).read(turn.messages):
                    result = {k: v for k, v in result.items() if k != "skill"}
                    result.update(already_loaded=True, note="already shown above；正文已在当前上下文中")
            elif call.name == "load_skill" and call.arguments.get("resource") is not None:
                raise ToolError("resource is unavailable for this skill")
            elif wanted and _full_skill_visible(turn, wanted):
                result = {"ok": True, "name": wanted, "already_loaded": True}
            elif call.name in DOCUMENT_CONTRACTS and (invalid := document_argument_error(call.name, call.arguments)):
                result = invalid
            elif call.name == "view_document":
                spec = get_catalog().get(turn.run.get("model"))
                if not spec or not spec.supports({"vision"}):
                    raise ToolError("当前模型不支持图像理解，请选择视觉模型")
                binding = await self._interruptible(run_id, self.platform.document_input(run_id, call.name, call.arguments))
                if binding.get("pending"):
                    raise FileDependencyPending(
                        call.arguments["file_id"], "document-view", None, binding["deadline"], binding["operation_id"]
                    )
                if binding.get("error"):
                    reason = binding["error"]
                    result = document_failure(reason if reason in DOCUMENT_ERRORS else "document_view_failed")
                    binding = {"files": []}
                for file in binding["files"]:
                    parts = await self._image_content(run_id, turn.run, file, spec)
                    if not any(part.get("type") == "image_url" for part in parts):
                        raise ToolError("文档图像当前不可读取")
                    self._states[run_id].setdefault("pending_image_inputs", []).append(parts)
                if binding["files"]:
                    result = {**binding, "ok": True, "input": "image"}
            elif call.name == "view_attachment":
                spec = get_catalog().get(turn.run.get("model"))
                if not spec or not spec.supports({"vision"}):
                    raise ToolError("当前模型不支持图像理解；OCR只能读字，不能理解架构图、照片或布局，请告知用户改用视觉模型")
                binding = await self._interruptible(run_id, self.platform.library(run_id, "image-reference", call.arguments))
                if binding.get("pending"):
                    raise FileDependencyPending(call.arguments["file_id"], "image-reference", call.arguments.get("pages"), deadline)
                if binding.get("error"):
                    raise ToolError(binding["error"])
                else:
                    for file in binding["files"]:
                        parts = await self._image_content(run_id, turn.run, file, spec)
                        if not any(p.get("type") == "image_url" for p in parts):
                            raise ToolError("图片当前不可读取")
                        self._states[run_id].setdefault("pending_image_inputs", []).append(parts)
                    result = {**binding, "ok": True, "input": "image"}
            elif call.name in {"view_workspace_file", "review_artifact"}:
                import base64

                from .checkpoint_budget import encoded_size
                from .multimodal import image_part

                spec = get_catalog().get(turn.run.get("model"))
                vision = bool(spec and spec.supports({"vision"}))
                if call.name == "view_workspace_file" and not vision:
                    raise ToolError("当前模型不支持图像理解，请选择视觉模型后查看工作文件")
                if set(call.arguments) - {"path", "pages"} or not isinstance(call.arguments.get("path"), str):
                    raise ToolError("请提供工作区文件路径和最多四个页码")
                lease_id = await self._ensure_lease(turn, run_id)
                try:
                    viewed = await self._interruptible(
                        run_id,
                        (self.broker.review_artifact if call.name == "review_artifact" else self.broker.view_workspace_file)(
                            lease_id, call.arguments["path"], call.arguments.get("pages")
                        ),
                    )
                except ClientError as exc:
                    from ..contracts.workspace_view import VIEW_ERRORS

                    try:
                        code = json.loads(exc.body).get("detail")
                    except (ValueError, AttributeError):
                        code = None
                    reason = (
                        VIEW_ERRORS.get(code, VIEW_ERRORS["view_unavailable"]) if isinstance(code, str) else VIEW_ERRORS["view_unavailable"]
                    )
                    raise ToolError(
                        f"无法查看工作文件：{reason}（{code if isinstance(code, str) and code in VIEW_ERRORS else 'view_unavailable'}）"
                    ) from exc
                state = self._states[run_id]
                additional = sum(len(item) for item in viewed["images"]) if vision else 0
                if (
                    encoded_size(self._context(run_id).snapshot(turn.run.get("_image_files", {}))) + additional + 128_000
                    > settings.checkpoint_max_bytes
                ):
                    raise ToolError("当前执行记录空间不足，请减少查看页数或开始新任务")
                for page, encoded in zip(viewed["pages"] if vision else [], viewed["images"] if vision else [], strict=True):
                    part = image_part(base64.b64decode(encoded, validate=True), "image/jpeg")
                    state.setdefault("pending_image_inputs", []).append(
                        [{"type": "text", "text": f"工作文件 {call.arguments['path']} 第 {page} 页"}, part]
                    )
                result = {"ok": True, "pages": viewed["pages"], "input": "image"}
                if viewed.get("sheet_pages"):
                    result["sheet_pages"] = viewed["sheet_pages"]
                if call.name == "review_artifact":
                    result.update({k: v for k, v in viewed.items() if k != "images"})
                    result.update(
                        visual_review="pending_model_judgment" if vision else "no visual review available",
                        input="image" if vision else "structural_text",
                        instruction=(
                            "按原始任务检查每页：内容/数据/公式是否正确，图表是否与文字一致；图片比例、文字清晰度、"
                            "重叠/裁切/溢出、空白与分页、标题/页码/字体连续性。结合结构检查，逐页报告pass/fail/Unverified及具体证据。"
                            "只报告实际检查范围；修复失败项后重新检查。无视觉能力时只报告结构证据并明确no visual review，"
                            "不能把exit0、WARN、文件存在或结构通过当成全部质量通过。"
                        ),
                    )
            elif call.name in {"read_library_file", "read_document", "search_document"}:
                request = (
                    self.platform.library(run_id, "read", call.arguments)
                    if call.name == "read_library_file"
                    else self.platform.document_input(run_id, call.name, call.arguments)
                )
                result = await self._interruptible(run_id, request)
                if result.get("pending") and result.get("action") in {"document-ocr", "native-scope"}:
                    raise FileDependencyPending(
                        call.arguments["file_id"], result["action"], result.get("pages"), result["deadline"], result["operation_id"]
                    )
                if result.get("pending") and result.get("action") == "native-text":
                    state = self._states[run_id]
                    key = "native-text:" + call.arguments["file_id"]
                    wait_until = result.get("deadline", time.time() + 150) if call.name != "read_library_file" else time.time() + 150
                    deadline = state.setdefault("file_wait_deadlines", {}).setdefault(key, wait_until)
                    if time.time() >= deadline:
                        raise ToolError("文件文字准备超过截止时间，请告知用户处理失败")
                    raise FileDependencyPending(call.arguments["file_id"], "native-text", None, deadline)
                result = {"ok": not bool(result.get("error")), **result}
            elif call.name == "extract_text":
                result = await self._interruptible(run_id, self.platform.library(run_id, "extract-text", call.arguments))
                if result.get("ocr") in {"pending", "running"}:
                    raise FileDependencyPending(call.arguments["file_id"], "extract-text", call.arguments.get("pages"), deadline)
                result = {"ok": result.get("ocr") == "done", **result}
                if not result["ok"]:
                    result["error"] = "文字提取失败：" + str(result.get("reason") or result.get("ocr")) + "；不得反复提取或sleep等待"
            elif call.name == "ui_components":
                result = ui_components(call.arguments, self._states[run_id], html_enabled=settings.genui_html_enabled)
            elif call.name == "search_capabilities":
                user_skills = [
                    {"name": name, "description": entry.get("description", "")[:160]}
                    for name, entry in {**skills(), **turn.personal}.items()
                    if name not in turn.disabled_skills
                ]
                hits = discovery.search(call.arguments.get("query", ""), call.arguments.get("limit", 3), user_skills)
                result = {"ok": True, "capabilities": hits}
            elif wanted and wanted in skills():
                mode = (turn.run.get("options") or {}).get("mode", "work")
                result = {"ok": True, "skill": load_skill(wanted, mode=mode), "version": skills()[wanted]["version"], "mode": mode}
            elif wanted in turn.personal:
                if getattr(call, "expected_file_id", None) and turn.personal[wanted]["file_id"] != call.expected_file_id:
                    raise ToolError(f"技能 {wanted} 的版本已失效")
                with span("agent.tool", tool=tool):
                    text = await self._interruptible(run_id, self._read_personal(turn, run_id, wanted))
                    if (turn.run.get("options") or {}).get("mode") == "chat":
                        text += "\n当前对话没有沙箱，技能中的脚本步骤无法执行。"
                    result = {
                        "ok": True,
                        "file_id": turn.personal[wanted]["file_id"],
                        "skill": text,
                        "name": wanted,
                        "mode": (turn.run.get("options") or {}).get("mode", "work"),
                    }
            elif call.name == "load_skill":
                raise ToolError(f"技能 {wanted or ''} 不存在或已不可用")
            elif call.name == "run_subtasks":
                with span("agent.tool", tool=tool):
                    result = await self._subtasks(turn, run_id, call, step)
            else:
                needs_sandbox = call.name in SANDBOX_TOOLS or (call.name == "save_skill" and call.arguments.get("path"))
                lease_id = await self._ensure_lease(turn, run_id) if needs_sandbox else (turn.lease_id or "")
                ctx = ToolContext(
                    run_id,
                    self.platform,
                    turn.messages,
                    turn.net_budget,
                    mode=(turn.run.get("options") or {}).get("mode", "work"),
                    document_tools_version=catalog_version(turn.run.get("options")),
                )
                budget = self._budgets[run_id]
                budget.update()
                ctx.exec_lifetime_seconds = (
                    120 if budget.data.get("pause_step") is not None else max(1, settings.run_max_seconds - budget.data["active_seconds"])
                )
                if call.name == "bash":
                    ctx.command_handle = command_handle(run_id, invocation, command_text(call.arguments))
                with span("agent.tool", tool=tool):
                    try:
                        result = await self._interruptible(
                            run_id,
                            execute_tool(call.name, call.arguments, lease_id, self.broker, ctx),
                            steerable=call.name in {"bash", "bash_wait"},
                        )
                    except SteerArrived:
                        # Only the wait is abandoned: the command keeps running and its unread output stays behind the same offset.
                        result = {
                            "ok": True,
                            "handle": call.arguments.get("handle") or ctx.command_handle,
                            "running": True,
                            "steered": True,
                            "hint": "用户发来新消息，已停止等待；命令仍在后台运行。先处理新消息，需要时用相同 offset 继续 bash_wait。",
                        }
        except (RunCanceled, LeaseInterrupted, BudgetPause, FileDependencyPending):
            raise
        except ClientError as exc:
            if exc.status == 410:
                raise LeaseInterrupted() from exc
            document_tool = call.name in {"read_document", "search_document", "view_document"}
            try:
                detail = json.loads(exc.body).get("detail")
            except (ValueError, AttributeError):
                detail = None
            public_reason = detail if isinstance(detail, str) and detail in DOCUMENT_ERRORS else None
            if (
                exc.status == 409
                and call.name not in {"extract_text", "read_library_file", "view_attachment"}
                and not (document_tool and public_reason)
            ):
                raise
            log.warning("tool %s client request failed", call.name, exc_info=True)
            result = {"ok": False, "error": "工具请求失败"}
            if document_tool:
                reason = public_reason or {
                    404: "document_unavailable",
                    408: "document_run_deadline",
                    413: "document_input_limit",
                    422: "invalid_document_arguments",
                    429: "document_queue_full",
                    507: "derived_capacity_exceeded",
                }.get(exc.status, "document_operation_unavailable")
                result = document_failure(reason)
        except ToolError as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception:  # broker / sandbox failures stay in the loop
            log.exception("tool %s failed", call.name)
            result = {"ok": False, "error": "工具执行失败"}
        if call.name == "load_skill" and wanted and not result.get("already_loaded"):
            state = self._states[run_id]
            if result.get("ok"):
                entry = {"name": wanted, "kind": "base" if wanted in BASE_SKILLS else "personal" if wanted in turn.personal else "builtin"}
                if result.get("file_id"):
                    entry["file_id"] = result["file_id"]
                    entry["filename"] = turn.personal[wanted]["filename"]
                    entry["title"] = turn.personal[wanted].get("title") or wanted
                if result.get("version"):
                    entry["version"] = result["version"]
                if result.get("package_hash"):
                    entry["package_hash"] = result["package_hash"]
                if result.get("guide_revision"):
                    entry["guide_revision"] = result["guide_revision"]
                loaded = state.setdefault("loaded_skills", [])
                if not any(item["name"] == wanted for item in loaded):
                    loaded.append(entry)
            title = (turn.personal.get(wanted) or skills().get(wanted) or {}).get("title") or wanted
            await self._event(
                run_id,
                "agent.skill",
                {
                    "name": wanted,
                    "title": title,
                    "status": "loaded" if result.get("ok") else "failed",
                    "reason": getattr(call, "skill_reason", "model"),
                    **({"error": result.get("error")} if not result.get("ok") else {}),
                },
            )
        if call.name in SANDBOX_TOOLS or call.name == "load_skill":
            self._warm_view(turn, run_id)
        metrics.TOOL_SECONDS.labels(tool).observe(time.perf_counter() - tool_started)
        metrics.TOOL_CALLS.labels(tool, str(bool(result.get("ok"))).lower()).inc()
        # Only identities returned by authorization-enforcing platform tools grant
        # summary provenance. Arguments, file text and recall never do.
        if result.get("ok") and call.name in {
            "read_library_file",
            "copy_library_file",
            "document_view",
            "read_document",
            "search_document",
            "view_document",
        }:
            metadata = result.get("view") if call.name == "document_view" else result
            if isinstance(metadata, dict):
                source = {key: metadata[key] for key in ("file_id", "block_id") if isinstance(metadata.get(key), str)}
                sources = self._states[run_id].setdefault("context_sources", [])
                if source and source not in sources:
                    if len(sources) >= 2048:
                        raise BudgetPause("来源数量超过上下文容量，已保存进度")
                    sources.append(source)
        shown = result.get("skill")
        await self._event(
            run_id,
            "agent.tool_result",
            {
                "step": step,
                "name": shown_name,
                "ok": bool(result.get("ok")),
                "exit_code": result.get("exit_code"),
                "stdout": (result.get("stdout") or (f"已读取技能说明（{len(shown)} 字）" if shown else ""))[:2000],
                **(
                    {
                        "running": bool(result.get("running")),
                        "job_key": hashlib.sha256(result["handle"].encode()).hexdigest() if result.get("handle") else None,
                        "idle_seconds": result.get("idle_seconds"),
                    }
                    if call.name in {"bash", "bash_wait", "bash_kill"}
                    else {}
                ),
                "stderr": (result.get("stderr") or "")[:2000],
                "error": result.get("error"),
                **(
                    {k: result[k] for k in ("ocr", "completed_pages", "text_only", "reason") if k in result}
                    if call.name == "extract_text"
                    else {}
                ),
                **({"sources": web_sources(result)} if call.name in {"web_search", "read_url"} else {}),
                **({"images": [i["path"] for i in result.get("images", [])]} if call.name == "search_images" else {}),
            },
        )
        if call.name in PREVIEW_AFTER:
            self._schedule_previews(turn, run_id)
        if call.name == "deliver_file" and result.get("ok"):
            turn.artifacts.append(result)
            path = call.arguments.get("path")
            if isinstance(path, str):
                turn.delivered[path] = {"sha256": result["sha256"], "size": result["size"]}
                self._states[run_id].setdefault("delivery_names", {})[path] = result.get("filename") or path.rsplit("/", 1)[-1]
        if call.name in {"view_attachment", "extract_text"}:
            from .stuck import fingerprint

            self._states[run_id].setdefault("file_wait_deadlines", {}).pop(
                fingerprint({"name": call.name, "arguments": call.arguments}), None
            )
        spec = get_catalog().get(turn.run.get("model"))
        if spec:
            result = admit_tool_result(
                ContextBand.for_model(spec, turn.run.get("context_settings")),
                turn.messages,
                _tools_for_run(turn.run),
                self._states[run_id],
                call,
                result,
                image_start=image_start,
            )
        return result

    async def _apply_steers(self, run_id: str, turn: _Turn, *, pending: bool | None = None) -> None:
        state = self._states[run_id]
        if pending is None:
            pending = (await self.platform.get_run_state(run_id)).get("steer_pending", False)
        if pending:
            claimed = await self.platform.claim_steers(run_id)
            self._steer_pending.discard(run_id)
            applied = state.setdefault("steer_ids", [])
            for row in claimed["items"]:
                if row["id"] in applied:
                    continue
                selections = row.get("skills")
                if selections is None:
                    selections = [{"name": row["skill"]}] if row.get("skill") else []
                message_run = {
                    **turn.run,
                    "prompt": row["prompt"],
                    "input_files": row.get("input_files") or [],
                    "doc_refs": row.get("doc_refs") or [],
                    "options": {**(turn.run.get("options") or {}), "skills": selections},
                }
                content = await self._user_content(run_id, message_run, turn.run.get("model"))
                turn.run.setdefault("_image_files", {}).update(message_run.get("_image_files") or {})
                state.setdefault("active_user_indices", []).append(len(state["messages"]))
                self._context(run_id).append({"role": "user", "content": content})
                applied.append(row["id"])
                required = [(item["name"], "picked", item.get("file_id")) for item in selections]
                required.extend((name, "office", None) for name in _office_skills(row.get("input_files") or []))
                if (turn.run.get("options") or {}).get("mode") == "work":
                    required.extend((name, "office", None) for name in _office_skills(row["prompt"], keywords=True))
                state.setdefault("pending_steer_skills", []).extend(required)
                state["loop_start"] = len(state["tool_log"])
                state.pop("loop_nudge", None)
                await self._checkpoint(run_id, turn)
        for name, reason, file_id in state.get("pending_steer_skills", []):
            await self._ensure_skill(turn, run_id, name, reason, file_id)
        if state.get("pending_steer_skills"):
            state["pending_steer_skills"] = []
            await self._checkpoint(run_id, turn)

    async def _checkpoint(self, run_id: str, turn: _Turn) -> None:
        if budget := self._budgets.get(run_id):
            budget.update()
        state = self._states[run_id]
        state.update(artifacts=turn.artifacts, delivered=turn.delivered)
        if turn.run.get("_last_model"):
            state["context_model"] = turn.run["_last_model"]
        compact_log(state)
        payload, exceeded = bounded_checkpoint(
            self._context(run_id).snapshot(turn.run.get("_image_files", {})), settings.checkpoint_max_bytes
        )
        saved = await self.platform.put_checkpoint(run_id, {"expected_step": self._steps.get(run_id, 0), "state": payload})
        self._steps[run_id] = saved["step"]
        if exceeded:
            state.clear()
            state.update(payload)
            turn.messages = state["messages"]
            turn.artifacts, turn.delivered = state["artifacts"], state["delivered"]
            raise BudgetPause(CAPACITY_NOTE)

    async def _workspace_checkpoint(self, run_id: str, turn: _Turn, *, force=False) -> None:
        if not turn.lease_id or not turn.lease_used:  # an unused early sandbox holds the restored workspace unchanged
            return
        state = self._states[run_id]
        when = (state.get("workspace") or {}).get("saved_at", 0)
        if not force and time.time() - when < WORKSPACE_DEBOUNCE_SECONDS:
            return
        snapshot = await self._snapshot(turn, run_id)
        if snapshot is not None:
            await self._record_workspace(run_id, turn, snapshot)

    async def _record_workspace(self, run_id: str, turn: _Turn, snapshot: dict) -> dict:
        state = self._states[run_id]
        state["workspace"] = {**snapshot, "delivered": dict(turn.delivered), "step": self._steps[run_id], "saved_at": time.time()}
        state["workspace_dirty"] = bool(state.get("background_jobs"))
        await self._checkpoint(run_id, turn)
        return state["workspace"]

    async def _heartbeat(self, run_id: str, turn: _Turn) -> None:
        """Renew the sandbox lease on its own clock, so a long model call or command cannot starve it.

        Only the Broker's 410 means the lease is gone. Anything else is retried on the next tick; the Broker's
        own expiry bounds how long a missed renewal can last, and then it answers 410.
        """
        while True:
            await asyncio.sleep(settings.runtime_heartbeat_seconds)
            if not turn.lease_id:
                continue
            try:
                async with asyncio.timeout(30):
                    await self.broker.renew(turn.lease_id)
            except Exception as exc:
                metrics.LEASE_HEARTBEAT_FAILURES.inc()
                if isinstance(exc, ClientError) and exc.status == 410:
                    self._lease_errors[run_id] = exc
                    return
                log.warning("lease renewal failed run=%s; retrying", run_id, exc_info=True)

    async def _release_turn(self, turn: _Turn) -> None:
        if turn.preview_task:
            turn.preview_task.cancel()
            await asyncio.gather(turn.preview_task, return_exceptions=True)
            turn.preview_task = None
        if turn.lease_task:  # awaited, not cancelled: a create_lease cut short would leave a sandbox nobody releases
            await asyncio.gather(turn.lease_task, return_exceptions=True)
            turn.lease_task = None
        if turn.view_warm_task:
            turn.view_warm_task.cancel()
            await asyncio.gather(turn.view_warm_task, return_exceptions=True)
            turn.view_warm_task = None
        if turn.lease_id:
            lease_id, turn.lease_id = turn.lease_id, None
            async with asyncio.timeout(65):
                await self.broker.release(lease_id)

    async def _durable_tool(self, turn: _Turn, run_id: str, call: ToolCall, step: int, invocation: str) -> dict:
        state = self._states[run_id]
        record = state["tool_log"].get(invocation)
        if record and "result" in record:
            return record["result"]
        if record and call.name in NON_REPLAYABLE:
            computed = command_handle(run_id, invocation, command_text(call.arguments)) if call.name == "bash" else ""
            handle = record.get("handle") or computed
            if not handle:
                return {"ok": False, "error": UNCERTAIN_EXEC, "interrupted": True}
            # The command may still be running in this sandbox: continue waiting on that execution.
            # If the sandbox lost it, the helper says so and nothing is started again.
            # The event keeps the original tool name so the UI closes the step it opened before the restart.
            resumed = await self._run_tool(
                turn, run_id, ToolCall(call.id, "bash_wait", {"handle": handle}), step, invocation, event_name=call.name
            )
            # No observable result at all (lost execution, an unrecoverable exit state, or the wait itself
            # failed): the model must re-check the workspace, not re-run. A finished command, even with a
            # non-zero exit code, is a real result and passes through untouched.
            unobserved = resumed.get("exit_code") in (None, -1) and not resumed.get("running")
            if resumed.get("lost") or unobserved:
                return {"ok": False, "error": UNCERTAIN_EXEC, "interrupted": True, "handle": handle}
            return {**resumed, "resumed": True}
        if not record:
            record = {"name": call.name, "started": True}
            if call.name == "bash":
                # Deterministic, so a resumed run can wait on the same execution.
                record["handle"] = command_handle(run_id, invocation, command_text(call.arguments))
            state["tool_log"][invocation] = record
            if call.name in FILE_TOOLS:
                state["workspace_dirty"] = True
            await self._checkpoint(run_id, turn)
        if call.name == "ask_user":
            # An answer only ever satisfies the question it was given for; a stale one cannot leak into a later question.
            answered = state.get("answer") or {}
            if answered.get("question_id") == f"{run_id}:{invocation}":
                state.pop("answer")
                result = {"ok": True, "answer": answered["answer"]}
            else:
                args = call.arguments
                question = args.get("question")
                options = args.get("options", [])
                if (
                    not isinstance(question, str)
                    or not question.strip()
                    or len(question) > 16000
                    or not isinstance(options, list)
                    or len(options) > 30
                    or not all(isinstance(o, str) and len(o) <= 1000 for o in options)
                ):
                    return {"ok": False, "error": "question/options invalid"}
                q = {
                    "question_id": f"{run_id}:{invocation}",
                    "question": question,
                    "options": options,
                    "allow_text": args.get("allow_text", True) is not False,
                }
                # The sandbox is released next: its unsnapshotted files would be gone, so the snapshot is not debounced.
                await self._stop_background(run_id, turn)
                await self._workspace_checkpoint(run_id, turn, force=True)
                await self._checkpoint(run_id, turn)
                await self._release_turn(turn)
                await self.platform.pause_run(
                    run_id, {"status": RunStatus.WAITING_USER, "question": q, "result": {"sources": state["sources"]}}
                )
                raise RunPaused()
        elif call.name == "document_view":
            if call.name not in {t["function"]["name"] for t in _tools_for_run(turn.run)}:
                return {"status": "unavailable"}
            from pydantic import ValidationError

            from ..contracts.document_view import DocumentView

            args = call.arguments
            try:
                DocumentView.model_validate(args)
                if args.get("op") != "get_view" and bool(args.get("file_id")) == bool(args.get("path")):
                    raise ValueError("document target required")
                if args.get("op") == "reveal" and not args.get("targets"):
                    raise ValueError("reveal targets required")
            except (ValidationError, ValueError):
                return {
                    "error": "document_view: open需file_id或path；reveal需file_id或path + targets[{at, quote?}]，"
                    "quote放在targets内，不传filename；get_view可选view_id；capture需file_id或path + view_id/at/source"
                }
            spec = get_catalog().get(turn.run.get("model"))
            if args.get("op") == "capture" and (not spec or not spec.supports({"vision"})):
                return {"status": "unavailable", "reason": "model_vision_required"}
            identity = delivery_id_var.set(f"{run_id}:{invocation}")
            try:
                result = await self.platform.document_view(run_id, args)
                if result.get("status") == "capture":
                    view = result["view"]

                    async def capture():
                        reply = await self.platform.create_client_call(
                            run_id,
                            {
                                "call_id": f"{run_id}:{invocation}",
                                "kind": "capture_view",
                                "payload": {
                                    "input": {
                                        "view_id": view["view_id"],
                                        **({"at": args["at"]} if args.get("at") else {}),
                                        "max_width": args.get("max_width", 1600),
                                    },
                                    "timeout_ms": 10000,
                                },
                            },
                        )
                        deadline = time.monotonic() + 20
                        while reply["status"] in {"pending", "claimed"} and time.monotonic() < deadline:
                            await asyncio.sleep(0.25)
                            reply = await self.platform.get_client_call(run_id, f"{run_id}:{invocation}")
                        response = reply.get("result") or {}
                        return response.get("value") if response.get("ok") else {"status": "unavailable"}

                    result = await self._interruptible(run_id, asyncio.wait_for(capture(), timeout=20))
                if result.get("status") == "ready" and result.get("capture_id"):
                    file = {"capture_id": result["capture_id"], "filename": "文档截图", "page": 1, "content_type": "image/png"}
                    from .multimodal import CaptureExpired

                    try:
                        parts = await self._image_content(run_id, turn.run, file, spec)
                    except CaptureExpired:
                        return {"status": "unavailable", "reason": "capture_expired"}
                    if not any(p.get("type") == "image_url" for p in parts):
                        return {"status": "unavailable", "reason": "capture_expired"}
                    state.setdefault("pending_image_inputs", []).append(parts)
            except (ClientError, TimeoutError):
                result = {"status": "unavailable"}
            finally:
                delivery_id_var.reset(identity)
        elif call.name == "run_js":
            if call.name not in {t["function"]["name"] for t in _tools_for_run(turn.run)}:
                return {"ok": False, "error": {"type": "unavailable", "message": "当前运行没有可用的浏览器执行环境"}}
            args = call.arguments
            code, timeout = args.get("code"), args.get("timeout_ms", 2000)
            try:
                valid = (
                    isinstance(code, str)
                    and len(code.encode()) <= settings.client_js_code_max_bytes
                    and len(json.dumps(args.get("input"), ensure_ascii=False, allow_nan=False).encode())
                    <= settings.client_js_input_max_bytes
                    and isinstance(timeout, int)
                    and not isinstance(timeout, bool)
                    and 100 <= timeout <= settings.client_js_max_timeout_ms
                )
            except (ValueError, TypeError):
                valid = False
            if not valid:
                return {"ok": False, "error": {"type": "runtime", "message": "代码、输入或超时参数超出限制"}}
            call_id = f"{run_id}:{invocation}"

            async def wait_for_client():
                call_state = await self.platform.create_client_call(
                    run_id, {"call_id": call_id, "kind": "js", "payload": {"code": code, "input": args.get("input"), "timeout_ms": timeout}}
                )
                deadline = time.monotonic() + timeout / 1000 + 12
                while call_state["status"] in {"pending", "claimed"} and time.monotonic() < deadline:
                    await asyncio.sleep(0.25)
                    call_state = await self.platform.get_client_call(run_id, call_id)
                return call_state.get("result") or {
                    "ok": False,
                    "error": {"type": "unavailable", "message": "浏览器执行等待超时，请告知用户或使用其他方法"},
                }

            result = await self._interruptible(run_id, wait_for_client())
        elif call.name == "update_plan":
            steps = call.arguments.get("steps")
            if (
                set(call.arguments) != {"steps"}
                or not isinstance(steps, list)
                or len(steps) > 100
                or not all(
                    isinstance(s, dict)
                    and set(s) == {"id", "title", "status"}
                    and isinstance(s.get("id"), str)
                    and 1 <= len(s["id"]) <= 64
                    and isinstance(s.get("title"), str)
                    and len(s["title"]) <= 500
                    and s.get("status") in {"pending", "running", "done", "skipped", "failed"}
                    for s in steps
                )
            ):
                return {"ok": False, "error": "invalid plan"}
            state["plan"] = steps
            await self._checkpoint(run_id, turn)
            await self._event(run_id, "plan.updated", {"steps": steps})
            result = {"ok": True}
        else:
            identity = delivery_id_var.set(f"{run_id}:{invocation}")
            try:
                result = await self._run_tool(turn, run_id, call, step, invocation)
            finally:
                delivery_id_var.reset(identity)
        return result

    async def _stop_background(self, run_id, turn):
        if not turn.lease_id:
            return
        state = self._states[run_id]
        # Older checkpoints may have no Agent-side handles; the lease registry owns all processes.
        if state.get("background_jobs"):
            state["workspace_dirty"] = True
        try:
            await background_exec("exec_kill_all", {}, turn.lease_id, self.broker)
        except Exception:
            log.warning("background cleanup failed run=%s; lease release will destroy processes", run_id, exc_info=True)
            state["workspace_dirty"] = True
        state["background_jobs"] = []

    async def _take_reclaimed_workspace(self, run_id: str, turn: _Turn) -> bool:
        """When the Broker took this sandbox back for another run, make the workspace it saved the checkpoint's.

        The saved archive is newer than the debounced snapshot, so resuming from it loses nothing; without it
        the resume falls back to the last snapshot and says so.
        """
        if not turn.lease_id:
            return False
        try:
            deadline = time.monotonic() + RECLAIM_WAIT_SECONDS
            while True:
                lease = await self.broker.lease_status(turn.lease_id)
                if lease.get("reason") != "evicted_idle":
                    return False
                if lease.get("eviction_done") or lease.get("state") in {"released", "failed"} or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(2)
            state = self._states[run_id]
            state["background_jobs"] = []  # they died with the sandbox
            self._context(run_id).append({"role": "system", "content": WORKSPACE_RECLAIMED_NOTE})
            saved = lease.get("eviction_snapshot")
            if saved:
                await self._record_workspace(run_id, turn, {"key": saved["key"], "size": saved["size"]})
            else:
                state["workspace_dirty"] = True
                await self._checkpoint(run_id, turn)
            return True
        except Exception:
            log.warning("could not take over the workspace saved on reclaim run=%s", run_id, exc_info=True)
            return False

    async def _budget_pause(self, run_id, turn, reason, *, pause=None, preserve_jobs=False):
        budget = self._budgets[run_id]
        budget.update()
        budget.suspended = True  # saving a stalled step must not immediately trip the watchdog again
        state = self._states[run_id]
        preserve_jobs = preserve_jobs and bool(state.get("background_jobs"))
        # Preserve pending paid tools. Started non-replayable tools remain uncertain on resume.
        state["finished"] = False
        if turn.lease_id and not preserve_jobs:
            await self._stop_background(run_id, turn)
            try:
                await self._finish_previews(turn, run_id)
                if pause is None:
                    await self._deliver_leftovers(run_id, turn)
                await self._workspace_checkpoint(run_id, turn, force=True)
            except Exception as exc:
                if isinstance(exc, ClientError) and exc.status == 409:
                    raise
                state["workspace_dirty"] = True
                log.warning("pause cleanup incomplete run=%s", run_id, exc_info=True)
        await self._checkpoint(run_id, turn)
        await self._event(
            run_id,
            "agent.budget",
            {"kind": "paused", "minutes": round(self._budgets[run_id].data["active_seconds"] / 60, 1), "reason": reason},
        )
        saved = state.get("workspace")
        await self.platform.pause_run(
            run_id,
            {
                "status": RunStatus.INTERRUPTED,
                "error": reason,
                "auto_resume": False,
                "summary": state["final_summary"],
                "result": {
                    "pause": pause or {"kind": "budget"},
                    **({"delivery_incomplete": state["delivery_incomplete"]} if state.get("delivery_incomplete") else {}),
                    "sources": state["sources"],
                    "artifacts": turn.artifacts,
                    "plan": state["plan"],
                    "previews": list(turn.previews.values()),
                    "workspace_saved": bool(saved) and not state["workspace_dirty"],
                    "workspace_key": saved["key"] if saved else None,
                },
            },
        )
        if preserve_jobs and state.get("background_jobs") and turn.lease_id:
            # Broker retains bounded ownership until its original TTL/max lifetime;
            # a resumed run reacquires the same active lease. Never renew forever.
            lease_id, turn.lease_id = turn.lease_id, None
            try:
                await self.broker.renew(lease_id)
            except Exception:
                log.warning("retained pause renewal failed run=%s", run_id, exc_info=True)

    async def process(self, run_id: str, attempt: int = 1, *, attempt_id: str | None = None, checkpoint: dict | None = None) -> None:
        if not attempt_id:
            raise ValueError("a claimed attempt_id is required")
        identity = attempt_id_var.set(attempt_id)
        try:
            await self._process(run_id, attempt, attempt_id, checkpoint)
        finally:
            attempt_id_var.reset(identity)
            self._module_views.pop(run_id, None)
            self._states.pop(run_id, None)
            self._turns.pop(run_id, None)
            self._steps.pop(run_id, None)
            self._lease_errors.pop(run_id, None)
            self._steer_pending.discard(run_id)
            self._budgets.pop(run_id, None)

    async def _process(self, run_id, attempt, attempt_id, checkpoint):
        run = await self.platform.get_run(run_id)
        if run["status"] in RunStatus.TERMINAL | {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}:
            return
        checkpoint = checkpoint or await self.platform.get_checkpoint(run_id)
        state = restored_state(checkpoint) if checkpoint else fresh_state()
        compact_log(state)
        self._states[run_id] = state
        self._steps[run_id] = checkpoint["step"] if checkpoint else 0
        if checkpoint and checkpoint.get("answer") is not None:
            state["answer"] = checkpoint["answer"]
        run["_attempt_id"] = attempt_id
        window = run.get("budget_window_id") or run_id
        budget = self._budgets[run_id] = WorkBudget(state, window)
        turn = _Turn(run)
        self._turns[run_id] = turn
        turn.disabled_skills = frozenset(run.get("disabled_skills") or [])
        turn.personal = {s["name"]: s for s in run.get("personal_skills") or []}
        turn.artifacts, turn.delivered = state["artifacts"], state["delivered"]
        title_task = None
        heartbeat = asyncio.create_task(self._heartbeat(run_id, turn))
        run_started, outcome = time.perf_counter(), "error"
        try:
            await self.platform.transition_run(
                run_id,
                RunStatus.RUNNING,
                events=[{"type": "agent.started", "payload": {"model": run.get("model"), "steps": 0}}],
            )
            if attempt > 1:
                # The job was claimed again after an earlier attempt died. Its checkpoint, if any, decides what it resumes from.
                metrics.AGENT_RECOVERED.inc()
                note = "previous agent attempt did not finish; " + (
                    "continuing from its last checkpoint" if checkpoint else "restarting the task"
                )
                await self._event(run_id, "agent.recovered", {"attempt": attempt, "note": note})
            if checkpoint:
                if await self._restore_images(run_id, run, state):
                    await self._checkpoint(run_id, turn)
                metrics.RUN_RESUMES.labels("checkpoint").inc()
                workspace = state.get("workspace")
                if workspace and state["workspace_dirty"]:
                    self._context(run_id).append({"role": "system", "content": WORKSPACE_LAG_NOTE})
                elif not workspace and any(r.get("name") in FILE_TOOLS and "result" in r for r in state["tool_log"].values()):
                    # Earlier sandbox work was never snapshotted; say so rather than let the model assume it survived.
                    self._context(run_id).append({"role": "system", "content": WORKSPACE_MISSING_NOTE})
                if workspace:
                    await self._ensure_lease(turn, run_id, required=True)  # fail explicitly before reusing completed file operations
            prior = await self.platform.get_run_usage(run_id)
            run["_ledger_tokens"] = prior["prompt_tokens"] + prior["completion_tokens"]
            if run.get("needs_conversation_title"):
                run["_main_model_reserved"] = asyncio.Event()
                title_task = asyncio.create_task(self._conversation_title(run_id, run))
            model = run.get("model") or ""
            llm = self.llm or llm_for(model)  # fails the run early if the model is gone from the catalog
            options = run.get("options") or {}
            mode, skill = options.get("mode", "work"), options.get("skill")
            if "tool_catalog_v1" not in state:
                state["tool_catalog_v1"] = _tools_for_run(run)
            run["_fixed_catalog"] = state["tool_catalog_v1"]
            turn.disabled_skills = frozenset(run.get("disabled_skills") or [])
            turn.personal = {s["name"]: s for s in run.get("personal_skills") or []}
            if state.get("checkpoint_capacity_exceeded"):
                raise BudgetPause(CAPACITY_NOTE)
            await self._context(run_id).build(run, turn, model, llm)
            messages = state["messages"]
            if not state.get("skill_initialization_done"):
                self._register_carried_skills(turn, run_id)
                selected = options.get("skills")
                if selected is None:
                    selected = [{"name": skill, "title": options.get("skill_title") or skill}] if skill else []
                required = [(item["name"], "picked", item.get("file_id")) for item in selected]
                required.extend((name, "office", None) for name in _office_skills(run.get("input_files") or []))
                if mode == "work":
                    required.extend((name, "office", None) for name in _office_skills(run.get("prompt") or "", keywords=True))
                for name, reason, file_id in required:
                    await self._ensure_skill(turn, run_id, name, reason, file_id)
                state["skill_initialization_done"] = True
                await self._checkpoint(run_id, turn)
            spec = get_catalog().get(model)
            # chat mode, or a model without tool calling: words only, so never a sandbox
            catalog = _tools_for_run(turn.run)
            state.setdefault("internal_skill_versions", {})
            for internal_name, version in state["internal_skill_versions"].items():
                internal_skills.load(internal_name, version)
            offered = [] if spec is not None and not spec.supports({"tools"}) else catalog
            turn.allowed_tools = {t["function"]["name"] for t in offered} | {"finish"}
            turn.offered = offered
            self._prepare_office_turn(turn, run_id)
            error = None
            pause_reason = None
            step_limit = budget.data["start_step"] + settings.max_agent_steps
            while state["next_step"] < step_limit and not state["finished"]:
                kinds = budget.boundary()
                if kinds:
                    state["time_note_pending"] = BUDGET_WRAP_NOTE if "wrap_up" in kinds else REVIEW_NOTE
                    for kind in kinds:
                        if kind == "self_check":
                            continue
                        await self._event(
                            run_id,
                            "agent.budget",
                            {
                                "kind": kind,
                                "minutes": round(budget.data["active_seconds"] / 60, 1),
                                "reason": budget.data.get("reason", ""),
                            },
                        )
                    await self._checkpoint(run_id, turn)
                step = state["next_step"]
                if run_id in self._lease_errors:
                    raise LeaseInterrupted()
                if await self._is_canceled(run_id):
                    raise RunCanceled()
                # A resumed checkpoint can already contain a paid response and pending tools.
                # Enforce the budget before either executing those tools or dispatching another call.
                if run["_ledger_tokens"] >= settings.run_max_tokens:
                    raise TokenLimit(_token_limit_error(run))
                if not state["pending"] and state.get("pending_office_skills"):
                    for office_name in state["pending_office_skills"]:
                        await self._ensure_skill(turn, run_id, office_name, "office")
                    state["pending_office_skills"] = []
                    await self._checkpoint(run_id, turn)
                if not state["pending"]:
                    for parts in state.pop("pending_image_inputs", []):
                        state.setdefault("tool_image_indices", []).append(len(messages))
                        messages.append({"role": "user", "content": parts})
                    if note := state.pop("time_note_pending", None):
                        messages.append({"role": "user", "content": note})
                    if state.pop("budget_note_pending", False):
                        state["loop_start"] = len(state["tool_log"])
                        state.pop("loop_nudge", None)
                        messages.append(
                            {
                                "role": "user",
                                "content": "（平台提示：用户已继续任务，本轮已开启新的时间和步数窗口。请检查已保存的计划和文件，"
                                "继续未完成的工作；此前收尾限制已解除。不要重复已确认交付或可能已执行的命令。）",
                            }
                        )
                    left = step_limit - step
                    if left == WRAP_UP_STEPS and settings.max_agent_steps > 2 * WRAP_UP_STEPS:
                        messages.append({"role": "user", "content": WRAP_UP_NOTE.format(n=left)})
                    await self._apply_steers(run_id, turn, pending=run_id in self._steer_pending)
                    reason = detect_loop(state["tool_log"], state.get("loop_start", 0))
                    if reason:
                        nudge = state.get("loop_nudge")
                        if nudge and len(state["tool_log"]) > nudge["at"]:
                            raise BudgetPause(STUCK_ERROR)
                        if not nudge:
                            state["loop_nudge"] = {"at": len(state["tool_log"]), "reason": reason}
                            messages.append({"role": "user", "content": STUCK_NOTE})
                            await self._event(run_id, "agent.budget", {"kind": "nudge", "reason": reason})
                            await self._checkpoint(run_id, turn)
                    turn.allowed_tools = {t["function"]["name"] for t in offered} | {"finish"}
                    if mode == "work" and settings.watchdog_mode != "off" and offered and state["tool_log"]:
                        await self._watchdog(llm, run_id, turn, model, offered, budget)
                    turn.offered = offered
                    await self._compact_work_context(run_id, turn, llm, model, offered)
                    response = await self._call_model(llm, run_id, run, model, messages, step, tools=offered)
                    if offered == [] and response.tool_calls:
                        log.warning("run %s: model asked for tools in a words-only turn; ignoring", run_id)
                    calls = response.tool_calls if offered != [] else []
                    metrics.record_genui(
                        response.content or "", mode, max_blocks=settings.genui_max_blocks, max_block_chars=settings.genui_max_block_chars
                    )
                    assistant = {"role": "assistant", "content": response.content or ""}
                    if calls:
                        assistant["tool_calls"] = [
                            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": _arguments_json(c.arguments)}}
                            for c in calls
                        ]
                    if response.reasoning_content:
                        assistant["reasoning_content"] = response.reasoning_content
                        turn.thinking.append(response.reasoning_content)
                        turn.thinking_seconds += response.thinking_seconds
                    if calls and spec:
                        band = ContextBand.for_model(spec, run.get("context_settings"))
                        placeholders = [tool_result_message(c.id, c.name, CANNOT_FIT_RESULT) for c in calls]
                        if band.cost([assistant, *placeholders]) > batch_budget(band, messages, offered, state):
                            messages.extend([assistant, *placeholders])
                            await self._checkpoint(run_id, turn)
                            raise ContextPause("工具参数超过完整批次预算，未执行工具；请缩小任务范围")
                    messages.append(assistant)
                    if response.content:
                        state["final_summary"] = response.content
                    state["pending"] = [{"id": c.id, "name": c.name, "arguments": c.arguments, "ordinal": i} for i, c in enumerate(calls)]
                    state["finished"] = not calls  # an answer that is already complete counts even if it crossed the budget
                    await self._checkpoint(run_id, turn)
                    if response.content:
                        await self._event(run_id, "agent.message", {"step": step, "content": response.content})
                    if calls and run["_ledger_tokens"] >= settings.run_max_tokens:
                        raise TokenLimit(_token_limit_error(run))
                while state["pending"]:
                    data = state["pending"][0]
                    call = ToolCall(data["id"], data["name"], data["arguments"])
                    invocation = f"{step}:{data['ordinal']}"
                    image_start = len(state.get("pending_image_inputs", []))
                    result = await self._durable_tool(turn, run_id, call, step, invocation)
                    if spec:
                        result = admit_tool_result(
                            ContextBand.for_model(spec, run.get("context_settings")),
                            messages,
                            offered,
                            state,
                            call,
                            result,
                            image_start=image_start,
                        )
                    if call.name in {"web_search", "read_url"}:
                        seen = {source["url"] for source in state["sources"]}
                        for source in web_sources(result):
                            if source["url"] not in seen and len(state["sources"]) < 50:
                                state["sources"].append(source)
                                seen.add(source["url"])
                    state["pending_office_skills"] = sorted(
                        set(state.get("pending_office_skills", [])) | set(_tool_office_skills(call.name, call.arguments, result))
                    )
                    state["tool_log"][invocation] = observation(call.name, call.arguments, result)
                    messages.append(tool_result_message(call.id, call.name, result))
                    if call.name == "memory":
                        self._context(run_id).observe_memory_result(result, call.id)
                    state["pending"].pop(0)
                    job_handle = result.get("handle") or call.arguments.get("handle")
                    if call.name in {"bash", "bash_wait", "bash_kill"} and job_handle:
                        jobs = set(state.get("background_jobs", []))
                        if result.get("running"):
                            jobs.add(job_handle)
                        else:
                            jobs.discard(job_handle)
                        state["background_jobs"] = sorted(jobs)
                    if call.name == "finish" and result.get("ok"):
                        state.update(finished=True, final_summary=result.get("summary") or state["final_summary"])
                    changed = call.name in FILE_TOOLS or (
                        call.name in {"bash_wait", "bash_kill"}
                        and not result.get("running")
                        and result.get("exit_code") is not None
                        and result.get("handle") not in state.get("snapshotted_execs", [])
                    )
                    if changed:
                        state["workspace_dirty"] = True
                        if call.name in {"bash_wait", "bash_kill"}:
                            state["snapshotted_execs"] = [*state.get("snapshotted_execs", [])[-7:], job_handle]
                    await self._checkpoint(run_id, turn)
                    if changed:
                        await self._workspace_checkpoint(run_id, turn)
                for parts in state.pop("pending_image_inputs", []):
                    state.setdefault("tool_image_indices", []).append(len(messages))
                    messages.append({"role": "user", "content": parts})
                for office_name in state.get("pending_office_skills", []):
                    await self._ensure_skill(turn, run_id, office_name, "office")
                state["pending_office_skills"] = []
                state["next_step"] += 1
                await self._checkpoint(run_id, turn)
            budget.update()
            if (
                not state["finished"]
                and budget.data["active_seconds"] >= settings.run_max_seconds
                and budget.data.get("pause_step") is None
            ):
                budget.wrap(f"已工作{settings.run_max_seconds / 3600:g}小时，已暂停，可继续")
            if not state["finished"] and budget.data.get("pause_step") is not None:
                pause_reason = budget.data["reason"]
            if error is None and not state["finished"]:
                if run["_ledger_tokens"] >= settings.run_max_tokens:
                    raise TokenLimit(_token_limit_error(run))
                else:
                    # Out of steps: rather than ending on a bare error, let the model tell the user where things stand.
                    if not state.get("out_of_steps_done"):
                        messages.append({"role": "user", "content": OUT_OF_STEPS_NOTE})
                        await self._apply_steers(run_id, turn, pending=run_id in self._steer_pending)
                        response = await self._answer_without_tools(llm, run_id, run, model, messages, settings.max_agent_steps, offered)
                        messages.append({"role": "assistant", "content": response.content, "reasoning_content": response.reasoning_content})
                        state["final_summary"] = response.content
                        metrics.record_genui(
                            response.content or "",
                            mode,
                            max_blocks=settings.genui_max_blocks,
                            max_block_chars=settings.genui_max_block_chars,
                        )
                        state["out_of_steps_done"] = True
                        await self._checkpoint(run_id, turn)
                        await self._event(run_id, "agent.message", {"step": settings.max_agent_steps, "content": response.content})
                        await self._event(run_id, "agent.warning", {"message": f"本轮用完了 {settings.max_agent_steps} 步"})
                    pause_reason = f"本轮已完成 {settings.max_agent_steps} 步，已暂停，可继续"
            if pause_reason:
                raise BudgetPause(pause_reason)
            result: dict = {
                "artifacts": turn.artifacts,
                "plan": state["plan"],
                "previews": list(turn.previews.values()),
                "sources": state["sources"],
            }
            genui_report = inspect_answer(state["final_summary"] or "", max_block_chars=settings.genui_max_block_chars)
            if genui_report["blocks"]:
                result["genui"] = {"format": FORMAT, "catalog": CATALOG_VERSION, "blocks": genui_report["blocks"]}
            if turn.lease_id and turn.lease_used:  # an unused early sandbox ends like a turn without one
                await self._stop_background(run_id, turn)
                # Whatever the outcome, don't lose finished work with the sandbox.
                await self._finish_previews(turn, run_id)
                result["previews"] = list(turn.previews.values())
                await self._deliver_leftovers(run_id, turn)
                if state.get("delivery_incomplete"):
                    result["delivery_incomplete"] = state["delivery_incomplete"]
                # Nothing changed since the last snapshot: that archive still describes the sandbox exactly.
                saved = state.get("workspace")
                if state["workspace_dirty"] or saved is None:
                    snapshot = await self._snapshot(turn, run_id)
                    saved = None if snapshot is None else await self._record_workspace(run_id, turn, snapshot)
                result["workspace_saved"] = saved is not None
                if saved is not None:
                    result.update(
                        workspace_saved_at=datetime.now(UTC).isoformat(),
                        workspace_bytes=int(saved.get("size") or 0),
                        workspace_key=saved["key"],
                    )
            if turn.thinking:
                text = "\n\n".join(turn.thinking)
                result["thinking"] = {"text": text[-THINKING_KEEP_CHARS:], "seconds": round(turn.thinking_seconds, 1)}
            if pause_reason:
                await self._budget_pause(run_id, turn, pause_reason)
                outcome = "interrupted"
            elif error is None:
                await self.platform.set_run_status(run_id, RunStatus.SUCCEEDED, summary=state["final_summary"] or "完成", result=result)
                outcome = "succeeded"
            else:
                await self.platform.set_run_status(run_id, RunStatus.FAILED, error=error, summary=state["final_summary"], result=result)
                outcome = "failed"
        except TokenLimit as exc:
            await self._stop_background(run_id, turn)
            if turn.lease_id:
                try:
                    await self._finish_previews(turn, run_id)
                    await self._deliver_leftovers(run_id, turn)
                    await self._workspace_checkpoint(run_id, turn, force=True)
                except Exception as cleanup_error:
                    if isinstance(cleanup_error, ClientError) and cleanup_error.status == 409:
                        raise
                    log.warning("token cap cleanup incomplete run=%s", run_id, exc_info=True)
            await self.platform.set_run_status(
                run_id,
                RunStatus.FAILED,
                error=str(exc),
                summary=state["final_summary"],
                result={"sources": state["sources"], "artifacts": turn.artifacts, "plan": state["plan"]},
            )
            outcome = "failed"
        except FileDependencyPending as exc:
            await self._stop_background(run_id, turn)
            await self._workspace_checkpoint(run_id, turn, force=True)
            await self._checkpoint(run_id, turn)
            await self._release_turn(turn)
            await self._event(run_id, "agent.file_wait", {"file_id": exc.dependency["file_id"], "action": exc.dependency["action"]})
            await self.platform.pause_run(
                run_id, {"status": RunStatus.INTERRUPTED, "error": "正在准备附件", "file_dependency": exc.dependency}
            )
            outcome = "interrupted"
        except BudgetPause as exc:
            await self._budget_pause(
                run_id, turn, exc.reason, pause=getattr(exc, "pause", None), preserve_jobs=getattr(exc, "preserve_jobs", False)
            )
            outcome = "interrupted"
        except RunPaused:
            outcome = "waiting_user"
        except LeaseInterrupted:
            # The most recent durable checkpoint remains authoritative. Do not
            # snapshot a lost lease or pretend an empty replacement restored it.
            reclaimed = await self._take_reclaimed_workspace(run_id, turn)
            try:
                await self._release_turn(turn)
            except Exception:
                log.warning("lost lease release will be reconciled run=%s", run_id)
            await self.platform.pause_run(
                run_id,
                {
                    "status": RunStatus.INTERRUPTED,
                    "error": (
                        "沙箱长时间没有操作，已被回收给其他任务；工作区已保存，将自动继续" if reclaimed else "工作区租约中断，将自动继续"
                    ),
                    "auto_resume": True,
                    "evicted": reclaimed,
                    "result": {"sources": state["sources"]},
                },
            )
            outcome = "interrupted"
        except RunCanceled:
            outcome = "canceled"
            await self._event(run_id, "agent.canceled", {})
        except asyncio.CancelledError:
            raise
        except ClientError as exc:
            if exc.status == 409:
                # Ownership was lost. No more business writes from this attempt.
                raise
            log.warning("agent run %s client request failed", run_id, exc_info=True)
            await self.platform.set_run_status(run_id, RunStatus.FAILED, error="服务请求失败", result={"sources": state["sources"]})
        except Exception:
            log.exception("agent run %s failed", run_id)
            await self._event(run_id, "agent.error", {"error": "运行失败"})
            await self.platform.set_run_status(run_id, RunStatus.FAILED, error="运行失败", result={"sources": state["sources"]})
        finally:
            try:
                if title_task is not None:
                    try:
                        # Terminal status is already visible. Keep ownership/client alive
                        # for the title's remaining budget, never restart its clock here.
                        if outcome in {"succeeded", "failed"} and run["_main_model_reserved"].is_set():
                            await title_task
                    except Exception:
                        log.debug("conversation title task failed", exc_info=True)
                    finally:
                        title_task.cancel()
                        await asyncio.gather(title_task, return_exceptions=True)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                try:
                    await self._release_turn(turn)
                except Exception:
                    log.warning("lease release failed run=%s", run_id, exc_info=True)
                metrics.AGENT_RUN_SECONDS.labels(outcome).observe(time.perf_counter() - run_started)
