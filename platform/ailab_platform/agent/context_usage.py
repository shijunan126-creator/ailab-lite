"""Provider usage anchors: checkpoint metadata, never model-visible content."""

import json
import math
from hashlib import sha256

from ..contracts.model_context import CONTEXT_ABI_VERSION, tool_catalog_fingerprint
from ..model_policy import estimator

MAX_ANCHORS = 32


def identity(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compatible(state, model, tools):
    return {
        "model": model,
        "epoch": state.get("context_epoch", 0),
        "abi": state.get("context_abi_version", CONTEXT_ABI_VERSION),
        "tools": tool_catalog_fingerprint(tools),
    }


def valid_anchors(state, model, messages, tools):
    key = compatible(state, model, tools)
    if key["abi"] != CONTEXT_ABI_VERSION:
        return []
    candidates = [
        a for a in state.get("prompt_anchors", []) if all(a.get(k) == v for k, v in key.items()) and 0 <= a["end"] <= len(messages)
    ]
    ends = {a["end"] for a in candidates}
    digests = {}
    digest = sha256(b"[")
    for index in range(max(ends, default=0) + 1):
        if index in ends:
            completed = digest.copy()
            completed.update(b"]")
            digests[index] = completed.hexdigest()
        if index < max(ends, default=0):
            if index:
                digest.update(b",")
            digest.update(json.dumps(messages[index], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    matches = {a["end"]: a for a in candidates if a["digest"] == digests[a["end"]]}
    return [matches[end] for end in sorted(matches)]


def request_tokens(state, model, messages, tools):
    anchors = valid_anchors(state, model, messages, tools)
    if not anchors:
        return estimator.estimate(model, {"messages": messages, "tools": tools})
    last = anchors[-1]
    # Subtract identical framing: only the appended message delta is estimated.
    delta = estimator.raw(messages[last["end"] :]) - estimator.raw([])
    return last["tokens"] + max(0, delta)


def record_usage(state, model, messages, tools, tokens):
    if not isinstance(tokens, int) or tokens <= 0:
        return
    key = compatible(state, model, tools)
    anchors = list(state.get("prompt_anchors", []))
    anchor = {**key, "end": len(messages), "digest": identity(messages), "tokens": tokens}
    anchors = [a for a in anchors if a != anchor]
    anchors.append(anchor)
    # Preserve the earliest measurement; coarsen old intervals instead of growing
    # checkpoint metadata with the number of calls.
    state["prompt_anchors"] = anchors[:1] + anchors[-(MAX_ANCHORS - 1) :] if len(anchors) > MAX_ANCHORS else anchors


class CandidatePricing:
    """Allocate each measured interval by raw weights, without learning a factor.

    The first interval includes tools and wire framing. Later intervals use the
    difference of provider counts. Splits are necessarily approximate because a
    provider reports only whole requests. Newly rendered blocks always use raw
    estimates, even when their text happens to equal a previous block.
    """

    def __init__(self, state, model, source, tools, *, aliases=()):
        self.model = model
        self.tools = tools
        self.costs = {}
        self.positions = {}
        for i, message in enumerate(source):
            self.positions.setdefault(id(message), []).append(i)
        if aliases:
            by_content = {}
            for i, message in enumerate(source):
                by_content.setdefault(identity(message), []).append(i)
            for message in aliases:
                if id(message) not in self.positions and identity(message) in by_content:
                    self.positions[id(message)] = by_content[identity(message)]
        self.base = None
        start, previous = 0, 0
        for anchor in valid_anchors(state, model, source, tools):
            end = anchor["end"]
            weights = [max(1, estimator.raw(m) - 32) for m in source[start:end]]
            base = estimator.raw({"messages": [], "tools": tools}) if start == 0 else 0
            total = base + sum(weights)
            measured = max(0, anchor["tokens"] - previous)
            if start == 0:
                self.base = measured * base / total
            for index, weight in enumerate(weights, start):
                self.costs[index] = measured * weight / total
            start, previous = end, anchor["tokens"]

    def cost(self, messages):
        if self.base is None:
            return estimator.estimate(self.model, {"messages": messages, "tools": self.tools})
        # Object identity distinguishes retained raw messages from regenerated
        # semantic/runtime/memory blocks. Index lists handle repeated objects.
        used = {}
        total = self.base
        for message in messages:
            key = id(message)
            offset = used.get(key, 0)
            positions = self.positions.get(key, [])
            index = positions[offset] if offset < len(positions) else None
            used[key] = offset + 1
            total += self.costs[index] if index in self.costs else max(1, estimator.raw(message) - 32)
        return math.ceil(total - 1e-9)
