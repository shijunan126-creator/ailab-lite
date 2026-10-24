"""Browser tests against a real stack: four Python services, Redis, and the Portal's production build.

Everything is real except the two things that cost money or need privileges: the sandbox is the mock
provider (a temp directory) and the models are the mock ones from tests/models.test.toml.

    AILAB_E2E=1 pytest e2e                       (needs Redis, Node, and Chromium - see below)

Environment:
    AILAB_TEST_REDIS_URL               default redis://127.0.0.1:6379/15 (a scratch database: keys are removed after)
    PLAYWRIGHT_CHROMIUM_EXECUTABLE     use this browser binary instead of the one `playwright install` fetched
    AILAB_E2E_BASE_PORT / _PORTAL_PORT default 18080..18083 and 13000
    AILAB_E2E_PORTAL_DIR               test another checkout of the Portal (needs its node_modules), e.g. a dependency-upgrade branch
    AILAB_E2E_LOG_DIR                  where service logs are copied (default platform/e2e-logs)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

PLATFORM = Path(__file__).resolve().parents[1]
PORTAL_SRC = Path(os.environ.get("AILAB_E2E_PORTAL_DIR") or PLATFORM / "portal")  # point elsewhere to try an upgrade
TOKEN = "e2e-api-token-0123456789abcdef"  # the static API token (scripts); people sign in with a password
USERNAME = "e2e-user"
PASSWORD = "e2e-password-1234"
SERVICE_TOKENS = {name: f"e2e-{name}-credential-0123456789abcdef0123" for name in ("api", "agent", "broker", "worker", "ops")}
SERVICE_TOKEN = SERVICE_TOKENS["ops"]
BASE_PORT = int(os.environ.get("AILAB_E2E_BASE_PORT", "18080"))
PORTAL_PORT = int(os.environ.get("AILAB_E2E_PORTAL_PORT", "13000"))
REDIS_URL = os.environ.get("AILAB_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
LOG_DIR = Path(os.environ.get("AILAB_E2E_LOG_DIR", PLATFORM / "e2e-logs"))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("AILAB_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="browser E2E: set AILAB_E2E=1 (needs Redis, Node and Chromium)")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@dataclass
class Stack:
    portal: str
    api: str
    broker: str
    agent: str
    worker: str
    token: str = TOKEN
    service_token: str = SERVICE_TOKEN
    log_dir: Path = LOG_DIR
    env: dict | None = None  # the environment the API runs with, for commands that must see the same database


def _wait(url: str, what: str, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{what} did not come up at {url}")


def _run_logged(command: list[str], cwd: Path, log: Path, timeout: int) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    # Stream to disk: a failed build must retain its output without buffering it all in RAM.
    with log.open("wb") as output:
        subprocess.run(command, cwd=cwd, check=True, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)


def _build_portal(workdir: Path) -> Path:
    """Build the Portal in a copy: the repo's tsconfig.json and any running Portal's .next stay untouched."""
    if not (PORTAL_SRC / "node_modules").is_dir():
        raise RuntimeError("platform/portal/node_modules is missing: run `npm ci` there first")
    _run_logged(["npm", "test"], PORTAL_SRC, LOG_DIR / "portal-tests.log", 60)
    dst = workdir / "portal"
    dst.mkdir()
    # "middleware.ts" was renamed "proxy.ts" in Next 16; copy whichever exists
    names = ("app", "components", "lib", "middleware.ts", "proxy.ts", "next.config.mjs", "tsconfig.json")
    for name in (*names, "package.json", "package-lock.json", "next-env.d.ts"):
        src = PORTAL_SRC / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst / name)
        else:
            shutil.copy2(src, dst / name)
    # Not a symlink: Turbopack (Next 16's default) refuses to resolve files outside the workspace root.
    # A hard-link copy is instant and stays inside it; fall back to a real copy across filesystems.
    linked = subprocess.run(["cp", "-al", str(PORTAL_SRC / "node_modules"), str(dst / "node_modules")], capture_output=True)
    if linked.returncode != 0:
        shutil.rmtree(dst / "node_modules", ignore_errors=True)
        shutil.copytree(PORTAL_SRC / "node_modules", dst / "node_modules", symlinks=True)
    _run_logged(["npm", "run", "build"], dst, LOG_DIR / "portal-build.log", 600)
    return dst


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    import redis

    work = tmp_path_factory.mktemp("e2e")
    tag = uuid.uuid4().hex[:8]
    ports = {"api": BASE_PORT, "broker": BASE_PORT + 1, "agent": BASE_PORT + 2, "worker": BASE_PORT + 3}
    data = work / "data"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AILAB_")}
    env.update(
        AILAB_ENV="test",
        AILAB_ENV_FILE=str(work / "no-such.env"),  # never read a developer's real .env (it may hold model keys)
        AILAB_DATA_DIR=str(data),
        AILAB_STORAGE_ROOT=str(data / "storage"),
        AILAB_DATABASE_URL=f"sqlite:///{data}/e2e.db",
        AILAB_REDIS_URL=REDIS_URL,
        AILAB_RUN_QUEUE=f"ailab:stream:e2e-runs-{tag}",
        AILAB_FILE_QUEUE=f"ailab:stream:e2e-files-{tag}",
        AILAB_SANDBOX_PROVIDER="mock",
        AILAB_IMAGE_SEARCH_PROVIDER="mock",
        AILAB_MODELS_FILE=str(PLATFORM / "tests" / "models.test.toml"),
        AILAB_API_BASE_URL=f"http://127.0.0.1:{ports['api']}",
        AILAB_BROKER_BASE_URL=f"http://127.0.0.1:{ports['broker']}",
        AILAB_DEV_TOKEN=TOKEN,
        AILAB_ADMIN_USERNAME=USERNAME,
        AILAB_ADMIN_PASSWORD=PASSWORD,
        AILAB_SERVICE_TOKEN="",
        AILAB_TRUSTED_PROXIES='["127.0.0.1/32"]',
        AILAB_SERVICE_CREDENTIALS=json.dumps(
            {name: "sha256:" + hashlib.sha256(token.encode()).hexdigest() for name, token in SERVICE_TOKENS.items()}
        ),
        AILAB_RUN_TIMEOUT_SECONDS="120",
        AILAB_REAPER_INTERVAL_SECONDS="3600",
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    procs: list[subprocess.Popen] = []
    logs = []

    def start(name: str, cmd: list[str], cwd: Path, extra_env: dict | None = None) -> None:
        log = open(LOG_DIR / f"{name}.log", "wb")
        logs.append(log)
        procs.append(subprocess.Popen(cmd, cwd=cwd, env={**env, **(extra_env or {})}, stdout=log, stderr=subprocess.STDOUT))

    try:
        portal_dir = _build_portal(work)
        for name, port in ports.items():
            start(
                name,
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    # the Agent serves a wrapper that adds one scripted model behaviour (see e2e/scripted_agent.py)
                    "e2e.scripted_agent:app" if name == "agent" else f"ailab_platform.{name}.main:app",
                    "--no-proxy-headers",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                PLATFORM,
                {"AILAB_SERVICE_NAME": name, "AILAB_SERVICE_CREDENTIAL": SERVICE_TOKENS[name]},
            )
        for name, port in ports.items():
            _wait(f"http://127.0.0.1:{port}/healthz", name)
        start(
            "portal",
            ["node", str(portal_dir / "node_modules/next/dist/bin/next"), "start", "-H", "127.0.0.1", "-p", str(PORTAL_PORT)],
            portal_dir,
            {"AILAB_API_BASE": f"http://127.0.0.1:{ports['api']}"},
        )
        _wait(f"http://127.0.0.1:{PORTAL_PORT}/login", "portal")
        yield Stack(
            env=env,
            portal=f"http://127.0.0.1:{PORTAL_PORT}",
            api=f"http://127.0.0.1:{ports['api']}",
            broker=f"http://127.0.0.1:{ports['broker']}",
            agent=f"http://127.0.0.1:{ports['agent']}",
            worker=f"http://127.0.0.1:{ports['worker']}",
        )
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        for log in logs:
            log.close()
        client = redis.Redis.from_url(REDIS_URL)
        for pattern in (f"ailab:stream:e2e-*-{tag}", "ailab:lease:*", "ailab:leases:active", "ailab:lock:*"):
            for key in client.scan_iter(pattern):
                client.delete(key)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        b = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "report_" + report.when, report)


@pytest.fixture()
def context(browser, request):
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
    try:
        yield ctx
    finally:
        failed = any(
            getattr(request.node, "report_" + phase, None) and getattr(request.node, "report_" + phase).failed
            for phase in ("setup", "call")
        )
        try:
            if failed:
                # Node ids can contain slashes/parameter values; use a stable bounded filename.
                name = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                ctx.tracing.stop(path=LOG_DIR / f"failure-{name}.zip")
                for index, page in enumerate(ctx.pages):
                    try:
                        page.screenshot(path=str(LOG_DIR / f"failure-{name}-{index}.png"), timeout=5000)
                    except Exception:
                        pass  # A crashed page must not hide the original test failure or prevent cleanup.
            else:
                ctx.tracing.stop()
        finally:
            ctx.close()


@pytest.fixture()
def page(context):
    return context.new_page()


def login(page, stack: Stack, username: str = USERNAME, password: str = PASSWORD, path: str = "/") -> None:
    page.goto(stack.portal + path)
    page.fill("input[name=username]", username)
    page.fill("input[type=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("input[type=password]", state="detached", timeout=15000)


@pytest.fixture()
def logged_in(page, stack):
    login(page, stack)
    wait_models(page)
    return page


def wait_models(page):
    page.get_by_role("button", name="模型", exact=True).wait_for(timeout=15000)


def select_model(page, model):
    from playwright.sync_api import expect

    name = {"test-fast": "Test Fast", "test-pro": "Test Pro"}[model]
    menu = page.get_by_role("button", name="模型", exact=True)
    menu.click()
    page.get_by_role("listbox", name="模型", exact=True).get_by_role("option").filter(has_text=name).click()
    expect(menu).to_contain_text(name)


def confirm_action(page):
    dialog = page.get_by_role("alertdialog")
    dialog.get_by_role("button").last.click()
    dialog.wait_for(state="detached")
