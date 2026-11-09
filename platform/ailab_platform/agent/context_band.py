"""One post-boundary policy and staged-request validator for every context path.

All costs include image charges. Nothing here mutates the active epoch;
callers own persistence and activate only the returned, measured candidate.
"""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256

from ..config import ContextBandSettings, settings
from ..contracts.context_summary import ContextSummary, ReadModule, canonical_summary
from ..model_policy import ModelPolicy, estimator
from .budgets import ContextPause
from .context_protocol import protocol_groups
from .context_rendering import block, loaded_modules, semantic_message
from .context_usage import CandidatePricing, request_tokens
from .skill_modules import SUMMARY_MARKER

STALE_BLOCKS = ("<conversation_checkpoint>", "<runtime_state>", "<loaded_modules>", "历史摘要（仅作背景）：")


CANNOT_FIT_RESULT = {
    "ok": False,
    "reason": "batch_budget",
    "error": "cannot fit: 整个工具批次超过上下文预算；请减少读取范围、页数或并行文件数，整文件不会截断",
}


def bounded_memory(band, memory):
    from .prompts import memory_prompt

    snapshot = deepcopy(memory)
    from datetime import UTC, datetime

    from ..memory_recall.policy import priority
    from ..memory_recall.store import stamp

    while band.cost(memory_prompt(snapshot)) > band.memory_budget:
        entries = snapshot.get("entries") or []
        optional = max(
            (i for i, entry in enumerate(entries) if entry.get("kind") not in {"user", "preference"}),
            key=lambda i: (
                priority(entries[i].get("kind"), entries[i].get("origin")),
                -stamp(entries[i].get("reinforced_at"), datetime.min.replace(tzinfo=UTC)).timestamp(),
                entries[i]["id"],
            ),
            default=None,
        )
        if optional is None:
            raise ContextPause("记忆固定结构超过上下文预算，已暂停")
        entries.pop(optional)
    return snapshot


@dataclass(frozen=True)
class ContextBand:
    model: str
    window: int
    lower: int
    upper: int
    ceiling: int
    aim: int
    trigger: int
    output: int
    safety: int

    @classmethod
    def for_model(cls, spec, config=None):
        defaults = {key: getattr(settings, key) for key in ContextBandSettings.model_fields}
        policy = ContextBandSettings.model_validate({**defaults, **(config or {})})
        window = spec.context_tokens
        trigger = ModelPolicy.compaction_threshold(spec, config)
        output = min(policy.next_output_reserve, spec.max_output_tokens, settings.model_max_output_tokens or spec.max_output_tokens)
        ceiling = min(
            policy.hard_ceiling_tokens,
            int(window * policy.ceiling_window_ratio),
            trigger - 1,
            window - output - policy.context_safety_tokens,
        )
        return cls(
            spec.id,
            window,
            min(policy.target_lower_tokens, int(window * policy.lower_window_ratio)),
            min(policy.target_upper_tokens, int(window * policy.upper_window_ratio)),
            ceiling,
            max(
                min(policy.target_lower_tokens, int(window * policy.lower_window_ratio)),
                min(policy.target_aim_tokens, policy.target_upper_tokens, int(window * policy.upper_window_ratio)),
            ),
            trigger,
            output,
            policy.context_safety_tokens,
        )

    def cost(self, value):
        return estimator.estimate(self.model, value)

    def allocation(self, maximum, ratio):
        return max(1, min(maximum, int(self.ceiling * ratio)))

    @property
    def summary_budget(self):
        return self.allocation(6000, 0.12)

    @property
    def memory_budget(self):
        return self.allocation(2000, 0.04)

    def digest(self, epoch, messages, state, tools):
        protected = {
            key: state.get(key)
            for key in (
                "plan",
                "delivered",
                "delivery_names",
                "background_jobs",
                "file_wait_deadlines",
                "workspace_dirty",
                "memory_observed",
            )
        }
        value = {"epoch": epoch, "input": messages, "protected": protected, "policy": self.__dict__, "tools": tools}
        return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _select(band, name, items, budget, *, required=False):
    """Bound presentation only; authoritative operational state is never sliced."""
    selected = []
    if not items:
        return {"items": [], "omitted_count": 0}
    for item in items:
        candidate = block(name, {"items": [*selected, item], "omitted_count": len(items) - len(selected) - 1})
        if band.cost(candidate) > budget:
            if required:
                raise ContextPause(f"活动状态 {name} 超过上下文预算，已暂停；请先完成或取消部分任务")
            break
        selected.append(item)
    result = {"items": selected, "omitted_count": len(items) - len(selected)}
    if band.cost(block(name, result)) > budget:
        raise ContextPause("固定上下文预算过小，已暂停；请使用更大的模型窗口")
    return result


def bounded_runtime(band, state):
    jobs = sorted(set(state.get("background_jobs") or []))
    dependencies = [{"key": key, "deadline": deadline} for key, deadline in sorted((state.get("file_wait_deadlines") or {}).items())]
    # Active handles cannot be discarded based on age or a model assertion.
    active_budget = band.allocation(2000, 0.04)
    active = _select(band, "active_operations", [*({"job": job} for job in jobs), *dependencies], active_budget, required=True)
    plan = state.get("plan") or []
    ordered = [step for step in plan if step.get("status") == "running"] + [
        step for step in reversed(plan) if step.get("status") != "running"
    ]
    visible_plan = _select(band, "plan", ordered, band.allocation(2000, 0.04))
    deliveries = state.get("delivered") or {}
    names = state.get("delivery_names") or {}
    visible_deliveries = _select(
        band,
        "deliveries",
        [{"path": path, "name": names.get(path, path.rsplit("/", 1)[-1])} for path in reversed(deliveries)],
        band.allocation(1500, 0.03),
    )
    return block(
        "runtime_state",
        {
            "active_operations": active,
            "plan": visible_plan,
            "confirmed_deliveries": visible_deliveries,
            "delivery_total": len(deliveries),
            "workspace": {"available": bool(state.get("workspace")), "dirty": bool(state.get("workspace_dirty"))},
        },
    )


def bounded_summary(band, semantic):
    """Bound semantic background deterministically after the single summary call."""
    result = semantic.model_copy(deep=True)
    result.read_modules = []
    # The schema/16KB gate is checked before this reduction. Keep complete facts,
    # not truncated source-bearing JSON or a growing chain of old checkpoints.
    fields = ("uncertainties", "facts", "decisions", "next_actions", "open_items", "constraints")
    while band.cost(semantic_message(result)) > band.summary_budget:
        field = next((name for name in fields if getattr(result, name)), None)
        if field:
            getattr(result, field).pop()
        elif result.objective:
            result.objective = result.objective[: len(result.objective) // 2]
        else:
            raise ContextPause("摘要固定结构超过上下文预算，已暂停")
    return result


def bounded_navigation(band, semantic, messages):
    index = _select(band, "loaded_modules", loaded_modules(messages), band.allocation(2000, 0.04))
    semantic.read_modules = [ReadModule(**entry) for entry in index["items"]]
    while len(canonical_summary(semantic).encode("utf-8")) > 16000 and semantic.read_modules:
        semantic.read_modules.pop()
        index["items"].pop()
        index["omitted_count"] += 1
    navigation = block("loaded_modules", index)
    return semantic, navigation


def semantic_groups(messages, protected=()):
    """Associate tool images and plain assistant continuation with their batch."""
    groups = []
    for start, end in protocol_groups(messages):
        message = messages[start]
        content = message.get("content")
        image = message.get("role") == "user" and isinstance(content, list) and any(p.get("type") == "image_url" for p in content)
        continuation = message.get("role") == "assistant" and not message.get("tool_calls")
        if groups and start not in protected and (image or continuation) and messages[groups[-1][0]].get("tool_calls"):
            groups[-1] = (groups[-1][0], end)
        else:
            groups.append((start, end))
    return groups


def active_users(messages, first, tool_images=()):
    if not isinstance(first, int):
        return set()
    return {i for i in range(first, len(messages)) if messages[i].get("role") == "user" and (i == first or i not in tool_images)}


@dataclass
class StagedContext:
    messages: list
    semantic: ContextSummary
    current_indices: list
    measurements: dict


def stage_context(band, prefix, semantic, prior, state, tools, protected=(), current=(), *, keep_latest=True, pricing_source=None):
    """Pack newest-first, then validate the exact staged wire request before CAS.

    Optional history may never raise a candidate above U. Latest complete unit is
    mandatory, except for the product's explicit irreducible-current-input case.
    """
    protected = set(protected)
    semantic = bounded_summary(band, semantic)
    runtime = bounded_runtime(band, state)
    semantic, navigation = bounded_navigation(band, semantic, prior)
    blocks = [semantic_message(semantic), runtime, navigation]
    raw_protected = [prior[i] for i in sorted(protected)]
    raw_current = [*raw_protected, *current]

    def compose(blocks, recent=()):
        return [*prefix, *raw_protected, *blocks, *recent, *current]

    fixed = compose(blocks)

    pricing = CandidatePricing(state, band.model, pricing_source or state.get("messages") or prior, tools, aliases=prior)

    def cost(messages):
        return pricing.cost(messages)

    mandatory = cost(fixed)
    reason = "band"
    groups = []
    for start, end in semantic_groups(prior, protected):
        if start < len(prefix) or any(i in protected for i in range(start, end)):
            continue
        group = prior[start:end]
        if any(
            m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and (m["content"].startswith(STALE_BLOCKS) or SUMMARY_MARKER in m["content"])
            for m in group
        ):
            continue
        groups.append(group)
    selected = []
    if mandatory > band.ceiling:
        blocks = blocks[:2]
        fixed = compose(blocks)
        mandatory = cost(fixed)
    if mandatory > band.ceiling:
        # Only irreducible current input can exceed H; optional navigation has
        # already yielded. Re-enter ordinary latest-unit retention when it fits.
        latest = groups[-1] if groups and keep_latest else []
        without_input = cost([*prefix, *blocks, *latest])
        if not raw_current or without_input > band.ceiling:
            raise ContextPause("固定上下文和活动状态超过安全容量，已暂停")
        if latest:
            selected = [latest]
        reason = "over_ceiling_current_input"
    elif groups:
        latest = groups[-1]
        if cost([*fixed, *latest]) > band.ceiling and len(blocks) > 2:
            blocks = blocks[:2]
            fixed = compose(blocks)
        if cost([*fixed, *latest]) > band.ceiling:
            raise ContextPause("最近完整工具组超过安全容量，已暂停；请缩小读取批次")
        selected = [latest]
        # Cache immutable group costs for linear planning. Final measurement is
        # authoritative, including JSON framing and image charges.
        estimate = cost([*fixed, *latest])
        for group in reversed(groups[:-1]):
            increment = max(0, cost([*fixed, *group]) - cost(fixed))
            if estimate + increment > min(band.aim, band.upper):
                break
            selected.append(group)
            estimate += increment
    selected.reverse()
    recent = [message for group in selected for message in group]
    replacement = compose(blocks, recent)
    total = cost(replacement)
    # Deterministic correction, never another paid summary. Only the latest unit
    # is mandatory. Remove optional groups oldest-first if framing prediction errs.
    while len(selected) > 1 and total > min(band.upper, band.ceiling):
        selected.pop(0)
        recent = [message for group in selected for message in group]
        replacement = compose(blocks, recent)
        total = cost(replacement)
    if total >= min(band.trigger, band.window - band.output - band.safety) or (
        total > band.ceiling and reason != "over_ceiling_current_input"
    ):
        raise ContextPause("摘要和当前输入超过安全容量，已暂停；请缩小任务范围")
    protocol_groups(replacement)
    images = sum(
        p.get("type") == "image_url" for m in replacement for p in (m.get("content") if isinstance(m.get("content"), list) else [])
    )
    measures = {
        "before_tokens": cost([*prior, *current]),
        "after_tokens": total,
        "checkpoint_tokens": band.cost(blocks[:1]),
        "runtime_tokens": band.cost(blocks[1:2]),
        "modules_tokens": band.cost(blocks[2:]),
        "recent_tokens": band.cost(recent),
        "current_input_tokens": band.cost(raw_current),
        "lower_tokens": band.lower,
        "target_tokens": band.aim,
        "upper_tokens": band.upper,
        "ceiling_tokens": band.ceiling,
        "retained_groups": len(selected),
        "retained_images": images,
        "reason": reason,
    }
    indices = [*range(len(prefix), len(prefix) + len(raw_protected)), *range(len(replacement) - len(current), len(replacement))]
    return StagedContext(replacement, semantic, indices, measures)


def admit_tool_result(band, messages, tools, state, call, result, *, image_start=0):
    """Admit the complete pending batch, including not-yet-inserted images.

    The actual execution result remains in the runtime ledger. A cannot-fit wire
    projection never licenses replay, hides a live handle, or truncates a file.
    """
    from .tools import tool_result_message

    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("tool_calls")), len(messages))
    batch = messages[start:]
    images = [{"role": "user", "content": parts} for parts in state.get("pending_image_inputs", [])]
    budget = batch_budget(band, messages, tools, state)
    # Reserve a minimal paired result for every remaining invocation. Do not let
    # early results exhaust the space required to close the protocol batch.
    pending = state.get("pending") or []
    reserve = band.cost([tool_result_message(item["id"], item["name"], CANNOT_FIT_RESULT) for item in pending[1:]])
    candidate = [*batch, tool_result_message(call.id, call.name, result), *images]
    full = [*messages, tool_result_message(call.id, call.name, result), *images]
    if band.cost(candidate) + reserve <= budget and request_tokens(state, band.model, full, tools) + reserve < band.window - min(
        4096, band.output * 2
    ) - min(2048, band.window // 16):
        return result
    if "pending_image_inputs" in state:
        del state["pending_image_inputs"][image_start:]
    # Keep bounded operational identifiers so follow-up operations remain possible.
    visible = {
        key: result[key]
        for key in ("handle", "running", "exit_code", "delivery_id", "file_id", "path", "next_cursor", "cursor")
        if key in result and isinstance(result[key], (str, int, bool, type(None)))
    }
    rejected = {**CANNOT_FIT_RESULT, **visible}
    if band.cost([*batch, tool_result_message(call.id, call.name, rejected), *images[:image_start]]) + reserve > budget:
        raise ContextPause("工具参数和活动句柄超过批次预算，已暂停；请缩小任务范围")
    return rejected


def batch_budget(band, messages, tools, state):
    protected = active_users(messages, state.get("user_message_index"), state.get("tool_image_indices") or []) | set(
        state.get("active_user_indices") or []
    )
    essentials = [*messages[:2], *(messages[i] for i in sorted(protected)), bounded_runtime(band, state)]
    floor = CandidatePricing(state, band.model, messages, tools).cost(essentials) + band.summary_budget + band.allocation(2000, 0.04)
    # The newest complete batch must survive the next boundary alongside every
    # mandatory component. A window fraction rejects files that actually fit.
    return max(0, band.ceiling - floor)
