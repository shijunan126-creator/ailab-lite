"""Opt-in Agent → Broker HTTP → real sandbox controlled-fetch acceptance."""

import asyncio
import hashlib
import io
import os
import shlex
import tarfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.agent import net_tools
from ailab_platform.broker import main
from ailab_platform.broker.service import LeaseManager
from ailab_platform.config import settings
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401
from tests.test_broker_lease import _Platform


def test_public_fetch_install_archive_rejection_and_skill_path(provider, tmp_path, monkeypatch):  # noqa: F811
    if os.environ.get("AILAB_TEST_PUBLIC_FETCH") != "1" or provider.name == "mock":
        pytest.skip("set AILAB_TEST_PUBLIC_FETCH=1 with disposable real backends")
    monkeypatch.setattr(settings, "broker_owner", "fetch-" + uuid.uuid4().hex)
    manager = LeaseManager(provider=provider, platform=_Platform(), storage=object(), store=MemoryLeaseStore(), start_background=False)
    monkeypatch.setattr(main, "get_manager", lambda: manager)
    headers = {"X-Service-Token": settings.service_token}

    with TestClient(main.app) as client:
        lease = manager.create("controlled-fetch", ttl_seconds=600)
        lease_id = lease["lease_id"]

        class Broker:
            async def put_fetched(self, ref, path, source):
                with source.open("rb") as f:
                    response = client.put(
                        f"/internal/leases/{ref}/fetched", params={"path": path}, headers=headers, content=iter(lambda: f.read(65536), b"")
                    )
                assert response.status_code == 200, response.text

            async def exec(self, ref, cmd, timeout):
                return asdict(await asyncio.to_thread(manager.exec, ref, cmd, timeout))

            async def get_file(self, ref, path):
                return await asyncio.to_thread(manager.get_file, ref, path)

        broker = Broker()

        async def run():
            budget = net_tools.Budget()
            url = "https://raw.githubusercontent.com/pallets/itsdangerous/main/LICENSE.txt"
            downloaded = await net_tools.download_url({"url": url}, lease_id, broker, {url}, budget)
            data = manager.get_file(lease_id, downloaded["path"])
            assert hashlib.sha256(data).hexdigest() == downloaded["sha256"]
            repo = await net_tools.fetch_repo({"repo": "pallets/itsdangerous", "ref": "2.2.0"}, lease_id, broker, budget)
            assert repo["ok"] and repo["entries"] > 10
            installed = await net_tools.fetch_packages({"packages": ["itsdangerous==2.2.0"]}, lease_id, broker, budget)
            assert installed["ok"], installed
            assert (await broker.exec(lease_id, "python -c 'import itsdangerous'", 15))["exit_code"] == 0

            skill = b"---\nname: contract-skill\ndescription: contract\n---\n# Contract\n"
            provider.put_file(lease["sandbox_id"], "/workspace/new/skill/SKILL.md", skill)
            saved = []

            class API:
                async def save_skill(self, *args):
                    saved.append(args)
                    return {"status": "pending"}

            result = await net_tools.save_skill(
                {"name": "contract-skill", "mode": "create", "path": "/workspace/new/skill/SKILL.md"},
                "controlled-fetch",
                lease_id,
                broker,
                API(),
            )
            assert result["status"] == "pending" and saved[0][-1] == skill

            # Only the malicious archive source is synthetic; injection/extraction
            # still traverse the real Broker route and provider.
            monkeypatch.setattr(net_tools, "REPO_MAX_FILES", 8)
            monkeypatch.setattr(net_tools, "REPO_MAX_BYTES", 1024)
            for attack in ("bomb", "count", "traversal", "symlink"):
                archive = tmp_path / f"{attack}.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    entry = tarfile.TarInfo("root/first.txt")
                    entry.size = 2
                    tar.addfile(entry, io.BytesIO(b"ok"))
                    for n in range(9 if attack == "count" else 1):
                        entry = tarfile.TarInfo("root/../../escape" if attack == "traversal" else f"root/a{n}")
                        if attack == "symlink":
                            entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
                            tar.addfile(entry)
                        else:
                            content = b"x" * (2048 if attack == "bomb" else 1)
                            entry.size = len(content)
                            tar.addfile(entry, io.BytesIO(content))

                @asynccontextmanager
                async def download(*args, **kwargs):
                    yield {
                        "path": archive,
                        "size": archive.stat().st_size,
                        "url": url,
                        "sha256": "synthetic",
                        "content_type": "application/gzip",
                    }

                monkeypatch.setattr(net_tools.web, "download", download)
                with pytest.raises(net_tools.NetToolError):
                    await net_tools.fetch_repo({"repo": f"contract/{attack}"}, lease_id, broker, net_tools.Budget())
                dest = shlex.quote(f"/workspace/repos/{attack}")
                assert (await broker.exec(lease_id, f"test ! -e {dest} && test ! -e {dest}.partial", 10))["exit_code"] == 0
            assert not list(manager._transfer_root.glob("transfer-*"))

        try:
            asyncio.run(run())
        finally:
            manager.release(lease_id)
        assert provider.inspect(lease["sandbox_id"]) is None
        assert not list(manager._transfer_root.glob("transfer-*"))
