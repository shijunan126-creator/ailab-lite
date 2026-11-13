"""Thinking that arrives inside the answer text as <think>...</think>, from servers without a reasoning parser."""

import asyncio
import json
import random

import httpx
import pytest

from ailab_platform.catalog import CatalogError, parse_catalog
from ailab_platform.llm import _StreamState
from ailab_platform.model_transport import ModelTransport
from ailab_platform.thinking import ThinkSplitter, split_think, thinking_disabled


def _split(chunks: list[str], prefilled: bool = False, tag: str = "think") -> tuple[str, str]:
    splitter = ThinkSplitter(tag, prefilled)
    answer = thinking = ""
    for chunk in chunks:
        a, t = splitter.feed(chunk)
        answer, thinking = answer + a, thinking + t
    a, t = splitter.finish()
    return answer + a, thinking + t


def _cuts(text: str) -> list[list[str]]:
    """Every way of cutting the text in two, and one cut per character."""
    return [[text[:i], text[i:]] for i in range(len(text) + 1)] + [list(text)]


@pytest.mark.parametrize("chunks", _cuts("<think>\n先看文件。\n</think>\n\n答案是 42。"))
def test_a_block_split_anywhere_gives_the_same_thinking_and_answer(chunks):
    assert _split(chunks) == ("答案是 42。", "先看文件。\n")


@pytest.mark.parametrize("chunks", _cuts("先看文件。</think>\n\n答案"))
def test_a_prefilled_template_sends_only_the_closing_tag(chunks):
    assert _split(chunks, prefilled=True) == ("答案", "先看文件。")


def test_a_prefilled_model_that_writes_the_opening_tag_again_is_accepted():
    assert _split(["\n<thi", "nk>\n想", "</think>答"], prefilled=True) == ("答", "想")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("在 HTML 里写 <think> 标签没有意义。", ("在 HTML 里写 <think> 标签没有意义。", "")),
        ("结束标签 </think> 只是文字", ("结束标签 </think> 只是文字", "")),
        ("<thinking> is another tag", ("<thinking> is another tag", "")),
        ("<", ("<", "")),
        ("  \n", ("  \n", "")),
    ],
)
def test_tags_after_the_answer_starts_are_literal_text(text, expected):
    for chunks in _cuts(text):
        assert _split(chunks) == expected


def test_a_partial_closing_tag_inside_thinking_is_only_held_until_decided():
    splitter = ThinkSplitter()
    assert splitter.feed("<think>a</") == ("", "a")
    assert splitter.feed("b") == ("", "</b")
    assert splitter.feed("<</thi") == ("", "<")
    assert splitter.feed("nk>答") == ("答", "")
    assert splitter.feed("<think>仍是正文") == ("<think>仍是正文", "")


def test_random_chunking_never_changes_the_result():
    rng = random.Random(7)
    text = "<think>\n" + "推理<b>步骤</b>，比较 a<b。" * 50 + "</think>\n\n" + "正文 <think> 字面量。" * 50
    expected = _split([text])
    assert expected[1].startswith("推理") and expected[0].startswith("正文")
    for _ in range(200):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 40)))
        chunks = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
        assert _split(chunks) == expected


def test_never_closed_prefill_is_the_answer():
    assert split_think("直接回答，没有思考。", "think", prefilled=True) == ("直接回答，没有思考。", "")
    state = _StreamState(ThinkSplitter(prefilled=True))
    assert state.feed({"choices": [{"delta": {"content": "直接回答"}}]}) == ("", "直接回答")
    state.feed({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    response = state.response()
    assert (response.content, response.reasoning_content) == ("直接回答", "")


def test_a_reasoning_field_turns_tag_splitting_off():
    state = _StreamState(ThinkSplitter(prefilled=True))
    assert state.feed({"choices": [{"delta": {"reasoning_content": "想"}}]}) == ("", "想")
    assert state.feed({"choices": [{"delta": {"content": "答 </think> 原样"}}]}) == ("答 </think> 原样", "")
    response = state.response()
    assert (response.content, response.reasoning_content) == ("答 </think> 原样", "想")


@pytest.mark.parametrize(
    "payload",
    [
        {"thinking": {"type": "disabled"}},
        {"chat_template_kwargs": {"enable_thinking": False}},
        {"enable_thinking": False},
        {"reasoning_effort": "none"},
    ],
)
def test_requests_that_switch_thinking_off_are_recognised(payload):
    assert thinking_disabled(payload)
    assert not thinking_disabled({"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": True}})


def _sse(*contents: str) -> httpx.Response:
    chunks = [{"choices": [{"delta": {"content": c}}]} for c in contents]
    chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _transport(response: httpx.Response, **kw) -> ModelTransport:
    return ModelTransport("http://gpustack.test/v1", transport=httpx.MockTransport(lambda request: response), **kw)


def test_stream_hands_thinking_and_answer_to_their_own_sinks_and_flushes_a_held_tail():
    seen: list[tuple[str, str]] = []

    async def text(t):
        seen.append(("text", t))

    async def thinking(t):
        seen.append(("think", t))

    client = _transport(_sse("想一", "想</", "think>\n答案 <"), think_tag="think", think_prefilled=True)
    r = asyncio.run(client.chat_stream([{"role": "user", "content": "x"}], [], "m", on_text=text, on_reasoning=thinking))
    assert seen == [("think", "想一"), ("think", "想"), ("text", "答案 <")]
    assert (r.content, r.reasoning_content) == ("答案 <", "想一想")
    # an answer that is only the start of a tag is held until the stream ends, then passed on
    seen.clear()
    r = asyncio.run(_transport(_sse("<th"), think_tag="think").chat_stream([], [], "m", on_text=text, on_reasoning=thinking))
    assert seen == [("text", "<th")] and r.content == "<th"


def test_non_streamed_calls_split_tags_and_read_any_reasoning_field():
    def reply(message):
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}], "usage": {}})

    tagged = _transport(reply({"content": "<think>想</think>答"}), think_tag="think")
    r = asyncio.run(tagged.chat([{"role": "user", "content": "x"}], [], "m"))
    assert (r.content, r.reasoning_content) == ("答", "想")
    vllm = _transport(reply({"content": "答", "reasoning": "想"}))
    r = asyncio.run(vllm.chat([{"role": "user", "content": "x"}], [], "m"))
    assert (r.content, r.reasoning_content) == ("答", "想")


def test_models_without_a_tag_leave_the_text_untouched():
    client = _transport(_sse("<think>不是思考</think>"))
    r = asyncio.run(client.chat_stream([{"role": "user", "content": "x"}], [], "m"))
    assert (r.content, r.reasoning_content) == ("<think>不是思考</think>", "")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"think_tag": "<think>"}, "think_tag"),
        ({"think_prefilled": True}, "think_prefilled"),
        ({"think_prefilled": "yes"}, "think_prefilled"),
    ],
)
def test_catalog_rejects_bad_think_settings(extra, message):
    model = {"id": "m", "provider": "openai", "base_url": "http://x/v1", **extra}
    with pytest.raises(CatalogError, match=message):
        parse_catalog({"models": [model]})
    if "think_tag" not in extra:
        assert parse_catalog({"models": [{**model, "think_tag": "think", "think_prefilled": True}]}).get("m").think_prefilled is True
