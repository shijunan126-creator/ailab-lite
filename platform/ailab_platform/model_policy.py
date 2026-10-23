"""Model selection and bounded, calibrated input estimates; no HTTP or ledger writes."""

from __future__ import annotations

import json
import re

from .catalog import Catalog, ModelSpec
from .config import settings
from .llm import LLMError


class TokenEstimator:
    def __init__(self):
        self.factors: dict[str, float] = {}

    def raw(self, value: object) -> int:
        images = 0

        def bounded(item):
            nonlocal images
            if isinstance(item, dict):
                if item.get("type") == "image_url":
                    images += 1
                    return {"type": "image_url"}
                return {key: bounded(val) for key, val in item.items()}
            if isinstance(item, list):
                return [bounded(val) for val in item]
            return item

        text = json.dumps(bounded(value), ensure_ascii=False)
        wide = len(re.findall(r"[^\x00-\x7f]", text))
        return images * settings.vision_input_tokens + 32 + wide + (len(text) - wide + 3) // 4

    def estimate(self, alias: str, value: object) -> int:
        return int(self.raw(value) * self.factors.get(alias, 1.0)) + 1

    def calibrate(self, alias: str, value: object, prompt_tokens: int) -> None:
        if prompt_tokens > 0:
            observed = prompt_tokens / self.raw(value)
            self.factors[alias] = max(1.0, min(8.0, max(observed * 1.1, self.factors.get(alias, 1.0) * 0.95)))


estimator = TokenEstimator()


class ModelPolicy:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def candidates(self, alias: str, required: set[str]) -> list[ModelSpec]:
        selected = self.catalog.get(alias)
        if selected is None or not selected.available:
            raise LLMError(f"model {alias!r} is not available")
        result = []
        seen = set()

        def visit(spec):
            if spec.id in seen:
                return
            seen.add(spec.id)
            if spec.available and spec.supports(required):
                result.append(spec)
            for target in spec.fallback:
                visit(self.catalog.get(target))

        visit(selected)
        if not result:
            raise LLMError("no model satisfies required capabilities")
        return result

    def summary_model(self) -> ModelSpec:
        available = self.catalog.available()
        choices = [s for s in available if s.provider != "mock"] or available
        if not choices:
            raise LLMError("no summary model available")
        return min(choices, key=lambda s: (s.weight, s.id))

    @staticmethod
    def retryable(exc: LLMError) -> bool:
        return exc.retryable

    @staticmethod
    def input_budget(spec: ModelSpec) -> int:
        return min(int(spec.context_tokens * 0.7), spec.context_tokens - spec.max_output_tokens)
