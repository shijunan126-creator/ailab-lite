"""Transient control-plane outages must not discard hours of owned work."""

import asyncio
from types import SimpleNamespace

import pytest

from ailab_platform.agent import main
from ailab_platform.clients import ClientError


@pytest.mark.parametrize("failures", [1, 2])
def test_transient_renew_errors_recover_and_refresh_deadline(monkeypatch, failures):
    monkeypatch.setattr(main, "settings", SimpleNamespace(job_lease_seconds=0.3))

    async def scenario():
        calls = 0

        class Platform:
            async def job_action(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls <= failures:
                    raise ClientError("POST", "/renew", 503, "restarting")

        async def work():
            await asyncio.sleep(0.65)  # longer than two original leases

        await main._with_job_heartbeat(work(), Platform(), "job", "token")
        assert calls > failures + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["stale", "outage", "hang"])
def test_ownership_loss_or_renew_deadline_stops_work_before_expiry(monkeypatch, failure):
    lease = 0.3
    monkeypatch.setattr(main, "settings", SimpleNamespace(job_lease_seconds=lease))

    async def scenario():
        clock = asyncio.get_running_loop()
        started = clock.time()
        stopped = None
        calls = 0

        class Platform:
            async def job_action(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if failure == "hang":
                    await asyncio.sleep(10)
                raise ClientError("POST", "/renew", 409 if failure == "stale" else 503, "stale_claim")

        async def work():
            nonlocal stopped
            try:
                await asyncio.sleep(10)
            finally:
                stopped = clock.time() - started

        with pytest.raises((ClientError, TimeoutError)):
            await main._with_job_heartbeat(work(), Platform(), "job", "token")
        assert stopped is not None and stopped < lease
        if failure == "stale":
            assert calls == 1
        else:
            assert calls >= 2 and stopped >= lease * 0.7

    asyncio.run(scenario())


def test_claim_response_time_is_not_added_to_ownership(monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(job_lease_seconds=0.3))

    async def scenario():
        clock = asyncio.get_running_loop()
        started = clock.time()
        stopped = None

        class Platform:
            async def job_action(self, *args, **kwargs):
                raise AssertionError("must stop before first scheduled renew")

        async def work():
            nonlocal stopped
            try:
                await asyncio.sleep(10)
            finally:
                stopped = clock.time() - started

        with pytest.raises(TimeoutError):
            await main._with_job_heartbeat(work(), Platform(), "job", "token", lease_started_at=started - 0.18)
        assert 0.04 < stopped < 0.12

    asyncio.run(scenario())
