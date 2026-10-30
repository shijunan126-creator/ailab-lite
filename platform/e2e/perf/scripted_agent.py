"""Test-only model seam; no production backend changes or synthetic browser SSE."""

import asyncio

from ailab_platform.agent import loop
from ailab_platform.llm import LLMResponse
from e2e.perf.model_data import TEXT, TRIGGER
from e2e.scripted_agent import QuestionThenAnswer, app  # noqa: F401


class PacedAnswer(QuestionThenAnswer):
    async def chat_stream(self, messages, tools, model, user_id=None, on_text=None, on_reasoning=None):
        prompt = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
        if prompt != TRIGGER:
            return await super().chat_stream(messages, tools, model, user_id, on_text, on_reasoning)
        # Two Unicode characters per 50 ms, not a claim about tokenizer tokens/s.
        if on_text:
            for offset in range(0, len(TEXT), 2):
                await on_text(TEXT[offset : offset + 2])
                await asyncio.sleep(0.05)
        return LLMResponse(content=TEXT, usage={"prompt_tokens": 20, "completion_tokens": 2000})


loop.llm_for = lambda model: PacedAnswer()
