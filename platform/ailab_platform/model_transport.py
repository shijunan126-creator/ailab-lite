"""OpenAI-compatible HTTP transport; selection and metered retries live in ModelPolicy/AgentLoop."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

from .config import settings
from .llm import RETRYABLE_STATUS, LLMClient, LLMError, LLMResponse, TextSink, _parse_tool_calls, _StreamState, _usage, model_call

log = logging.getLogger("ailab.llm")


class ModelTransport(LLMClient):
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        upstream_model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        echo_reasoning: bool = False,
        disable_reasoning_for_quiet: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.upstream_model = upstream_model  # what the upstream calls the model; None = send the id as is
        self._transport = transport
        # Thinking models (DeepSeek V4.x) reject a tool conversation unless each assistant turn carries its
        # reasoning_content back; other upstreams may reject the extra field. Off by default, and switched on
        # for good the first time the upstream asks for it (see _asks_for_reasoning).
        self.echo_reasoning = echo_reasoning
        self.disable_reasoning_for_quiet = disable_reasoning_for_quiet

    def _outgoing(self, messages: list[dict]) -> list[dict]:
        if self.echo_reasoning:
            # Turns the agent writes itself (a preselected skill's load_skill) have no reasoning, but the upstream
            # rejects a tool-call turn without the field; an empty one is accepted.
            return [
                {**m, "reasoning_content": ""}
                if m.get("role") == "assistant" and m.get("tool_calls") and "reasoning_content" not in m
                else m
                for m in messages
            ]
        return [{k: v for k, v in m.items() if k != "reasoning_content"} if "reasoning_content" in m else m for m in messages]

    def _asks_for_reasoning(self, exc: LLMError) -> bool:
        text = str(exc)
        if self.echo_reasoning or "reasoning_content" not in text or " 400" not in text:
            return False
        log.warning("upstream %s wants reasoning_content passed back; echoing it from now on", self.base_url)
        self.echo_reasoning = True
        return True

    async def _post(self, payload: dict, headers: dict) -> dict:
        """POST with bounded retries on transient failures (429 / 5xx / network)."""
        # An ambiguous retry can be a second billable inference. The Agent accounts
        # each dispatched call separately; unmetered callers retain transport retries.
        call = model_call.get()
        attempts = 1 if call is not None else settings.llm_max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(settings.llm_idle_timeout_seconds, connect=5), transport=self._transport
                ) as client:
                    if call is not None:
                        call.reached_model = True
                    resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS
                if call is not None and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                    call.reached_model = False
                if last:
                    raise LLMError(f"model gateway unreachable after {attempts} attempts: {exc!r}", retryable=True) from exc
                reason = repr(exc)
            else:
                if resp.status_code < 400:
                    return resp.json()
                if call is not None and 400 <= resp.status_code < 500 and resp.status_code != 408:
                    call.reached_model = False
                body = resp.text[:300]
                if resp.status_code not in RETRYABLE_STATUS or last:
                    raise LLMError(f"model gateway returned {resp.status_code}: {body}", retryable=resp.status_code in RETRYABLE_STATUS)
                reason = f"{resp.status_code} {body}"
            delay = settings.llm_retry_backoff_seconds * (2**attempt) * random.uniform(0.5, 1.5)
            log.warning("model call failed (%s); retry %d/%d in %.1fs", reason, attempt + 1, attempts - 1, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _request(
        self, messages: list[dict], tools: list[dict], model: str, user_id: str | None, stream: bool, max_tokens: int | None = None
    ) -> tuple[dict, dict]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if user_id:
            # The gateway's per-user token rate limit keys on this header; without
            # it requests are not limited at all.
            headers["x-user-id"] = user_id
        payload: dict[str, Any] = {
            "model": self.upstream_model or model,
            "messages": self._outgoing(messages),
            "temperature": 0.2,
            "stream": stream,
        }
        call = model_call.get()
        if call is not None:
            payload["max_tokens"] = call.max_output_tokens
            if call.quiet and self.disable_reasoning_for_quiet:
                payload["thinking"] = {"type": "disabled"}
            if call.run_id:
                headers["x-run-id"] = call.run_id
            headers["x-call-id"] = call.call_id
            call.actual_backend = self.upstream_model or model
        elif max_tokens:
            # Unmetered callers (the memory dream) bound their own output.
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload, headers

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        user_id: str | None = None,
        on_text: TextSink | None = None,
        on_reasoning: TextSink | None = None,
    ) -> LLMResponse:
        try:
            async with asyncio.timeout(settings.llm_total_timeout_seconds or settings.llm_timeout_seconds):
                try:
                    return await self._stream_once(messages, tools, model, user_id, on_text, on_reasoning)
                except LLMError as exc:
                    if not exc.output_started and self._asks_for_reasoning(exc):
                        if model_call.get() is None:
                            return await self._stream_once(messages, tools, model, user_id, on_text, on_reasoning)
                        exc.retryable = True
                    raise
        except TimeoutError as exc:
            raise LLMError("model total or first-output deadline exceeded", retryable=True) from exc

    async def _stream_once(self, messages, tools, model, user_id, on_text, on_reasoning) -> LLMResponse:
        """Server-sent events. Retried like chat() on transient failures, but only until the first text was passed on:
        after that a retry would show the user the start of the answer twice."""
        started = time.perf_counter()
        payload, headers = self._request(messages, tools, model, user_id, stream=True)
        # An ambiguous retry can be a second billable inference. The Agent accounts
        # each dispatched call separately; unmetered callers retain transport retries.
        call = model_call.get()
        attempts = 1 if call is not None else settings.llm_max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            state = _StreamState()
            try:
                async with (
                    asyncio.timeout(settings.ttft_timeout_seconds) as first_output,
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(settings.llm_idle_timeout_seconds, connect=5), transport=self._transport
                    ) as client,
                ):
                    if call is not None:
                        call.reached_model = True
                    async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as resp:
                        if resp.status_code < 400:
                            async for line in resp.aiter_lines():
                                if not line.startswith("data:") or line[5:].strip() == "":
                                    continue
                                if line[5:].strip() == "[DONE]":
                                    state.completed = True
                                    break
                                text, thinking = state.feed(json.loads(line[5:]))
                                tool_output = any(c["id"] or any(c["function"].values()) for c in state.calls.values())
                                if text or thinking or tool_output:
                                    first_output.reschedule(None)
                                    state.emitted = True
                                    if call is not None:
                                        if call.ttft_seconds is None:
                                            call.ttft_seconds = time.perf_counter() - started
                                        call.emitted = True
                                if thinking and on_reasoning:
                                    state.emitted = True
                                    await on_reasoning(thinking)
                                if text and on_text:
                                    state.emitted = True
                                    await on_text(text)
                            if state.completed:
                                return state.response()
                            if last or state.emitted:  # some of the answer was shown: a retry would repeat it
                                raise LLMError("model stream is incomplete: ended without a completion marker", retryable=True)
                            reason = "stream ended before the completion marker"
                        else:
                            if call is not None and 400 <= resp.status_code < 500 and resp.status_code != 408:
                                call.reached_model = False
                            body = (await resp.aread()).decode("utf-8", "replace")[:300]
                            if resp.status_code not in RETRYABLE_STATUS or last:
                                raise LLMError(
                                    f"model gateway returned {resp.status_code}: {body}", retryable=resp.status_code in RETRYABLE_STATUS
                                )
                            reason = f"{resp.status_code} {body}"
            except (LLMError, TimeoutError) as exc:
                if state.emitted:
                    if isinstance(exc, TimeoutError):
                        exc = LLMError("model deadline exceeded after output", retryable=True)
                    exc.output_started = True
                raise exc
            except (httpx.TransportError, json.JSONDecodeError) as exc:
                if call is not None and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                    call.reached_model = False
                if last or state.emitted:
                    error = LLMError(f"model stream broke: {exc!r}", retryable=True)
                    error.output_started = state.emitted
                    raise error from exc
                reason = repr(exc)
            delay = settings.llm_retry_backoff_seconds * (2**attempt) * random.uniform(0.5, 1.5)
            log.warning("model stream failed (%s); retry %d/%d in %.1fs", reason, attempt + 1, attempts - 1, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def chat(
        self, messages: list[dict], tools: list[dict], model: str, user_id: str | None = None, *, max_tokens: int | None = None
    ) -> LLMResponse:
        try:
            async with asyncio.timeout(settings.llm_total_timeout_seconds or settings.llm_timeout_seconds):
                return await self._chat(messages, tools, model, user_id, max_tokens=max_tokens)
        except TimeoutError as exc:
            raise LLMError("model total deadline exceeded", retryable=True) from exc

    async def _chat(
        self, messages: list[dict], tools: list[dict], model: str, user_id: str | None = None, *, max_tokens: int | None = None
    ) -> LLMResponse:
        try:
            data = await self._post(*self._request(messages, tools, model, user_id, stream=False, max_tokens=max_tokens))
        except LLMError as exc:
            if not self._asks_for_reasoning(exc) or model_call.get() is not None:
                raise
            data = await self._post(*self._request(messages, tools, model, user_id, stream=False, max_tokens=max_tokens))

        call = model_call.get()
        if call is not None:
            call.usage = _usage(data.get("usage") or {})
            call.actual_backend = str(data.get("model") or call.actual_backend)[:120]
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason") or "stop"
        if finish_reason == "length":
            raise LLMError("model output was truncated (finish_reason=length); raise max tokens or shorten the task")
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=_parse_tool_calls(message.get("tool_calls") or []),
            usage=_usage(data.get("usage") or {}),
            finish_reason=finish_reason,
            reasoning_content=message.get("reasoning_content") or "",
        )
