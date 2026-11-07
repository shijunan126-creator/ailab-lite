"""Resident OCR configuration without installing an inference engine in the API."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("backend", ["cpu", "cann"])
@pytest.mark.parametrize("profile_prefix", [None, "", "/tmp/diagnostic-profile"])
def test_node_profiling_requires_explicit_diagnostic_prefix(monkeypatch, backend, profile_prefix):
    spec = importlib.util.spec_from_file_location("rapidocr_executor", Path(__file__).parents[2] / "images/rapidocr/executor.py")
    executor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(executor)
    if profile_prefix is None:
        monkeypatch.delenv("OCR_PROFILE_PREFIX", raising=False)
    else:
        monkeypatch.setenv("OCR_PROFILE_PREFIX", profile_prefix)
    entries = {}
    opts = SimpleNamespace(enable_profiling=True, add_session_config_entry=lambda key, value: entries.update({key: value}))
    assert executor.configure_session_options(opts, backend) is opts
    assert opts.enable_profiling is bool(profile_prefix)
    assert getattr(opts, "profile_file_prefix", None) == (profile_prefix or None)
    assert entries == ({"session.disable_cpu_ep_fallback": "1"} if backend == "cann" else {})


@pytest.fixture()
def cancellation_executor(monkeypatch):
    import threading

    spec = importlib.util.spec_from_file_location("rapidocr_cancel_executor", Path(__file__).parents[2] / "images/rapidocr/executor.py")
    executor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(executor)
    monkeypatch.setenv("OCR_EXECUTOR_TOKEN", "test-token")
    executor._manifest = {"backend": "cpu"}
    calls = []
    executor._supervisor = SimpleNamespace(
        active_id=None,
        cancel=threading.Event(),
        start=lambda deadline: calls.append("start"),
        stop=lambda: calls.append("stop"),
        connection=SimpleNamespace(send_bytes=lambda data: calls.append("send"), poll=lambda: True, recv=lambda: {"text": "ok"}),
    )
    return executor, calls


@pytest.mark.parametrize("phase", ["before_body", "between_chunks", "after_body"])
def test_stop_receipt_during_upload_prevents_late_inference(cancellation_executor, phase):
    import asyncio
    import time

    from fastapi import HTTPException

    executor, calls = cancellation_executor

    async def scenario():
        async def stream():
            if phase == "before_body":
                assert await executor.cancel_operation("op", "Bearer test-token") == {"status": "stopped"}
            yield b"first"
            if phase == "between_chunks":
                await executor.cancel_operation("op", "Bearer test-token")
            yield b"last"
            if phase == "after_body":
                await executor.cancel_operation("op", "Bearer test-token")

        request = SimpleNamespace(stream=stream)
        with pytest.raises(HTTPException) as caught:
            await executor.recognize_image(request, "Bearer test-token", "op", time.time() + 120, "cpu")
        assert caught.value.status_code == 408
        assert calls == []
        assert await executor.cancel_operation("op", "Bearer test-token") == {"status": "stopped"}
        with pytest.raises(HTTPException) as duplicate:
            await executor.recognize_image(request, "Bearer test-token", "op", time.time() + 120, "cpu")
        assert duplicate.value.status_code == 409

    asyncio.run(scenario())


def test_cancel_before_post_and_receipt_capacity_do_not_evict_tombstones(cancellation_executor, monkeypatch):
    import asyncio
    import time

    from fastapi import HTTPException

    executor, calls = cancellation_executor
    monkeypatch.setattr(executor, "_OPERATION_LIMIT", 2)

    async def scenario():
        await executor.cancel_operation("late", "Bearer test-token")
        with pytest.raises(HTTPException) as caught:
            await executor.recognize_image(None, "Bearer test-token", "late", time.time() + 120, "cpu")
        assert caught.value.status_code == 409
        await executor.cancel_operation("other", "Bearer test-token")
        with pytest.raises(HTTPException) as full:
            await executor.cancel_operation("third", "Bearer test-token")
        assert full.value.status_code == 429
        assert executor._operations["late"]["canceled"]
        executor._operations["late"]["expires"] = time.monotonic() - 1
        await executor.cancel_operation("third", "Bearer test-token")
        assert len(executor._operations) == 2 and "late" not in executor._operations
        assert calls == []

    asyncio.run(scenario())


def test_success_receipt_prevents_duplicate_and_unrelated_cancel_preserves_new_work(cancellation_executor):
    import asyncio
    import time

    from fastapi import HTTPException

    executor, calls = cancellation_executor

    async def scenario():
        await executor.cancel_operation("old", "Bearer test-token")

        async def stream():
            yield b"image"

        async def disconnected():
            return False

        request = SimpleNamespace(stream=stream, is_disconnected=disconnected)
        assert await executor.recognize_image(request, "Bearer test-token", "new", time.time() + 120, "cpu") == {"text": "ok"}
        assert calls == ["start", "send"]
        with pytest.raises(HTTPException) as caught:
            await executor.recognize_image(request, "Bearer test-token", "new", time.time() + 120, "cpu")
        assert caught.value.status_code == 409
        assert calls == ["start", "send"]

    asyncio.run(scenario())
