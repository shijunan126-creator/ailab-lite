"""One bounded exec mechanism over the existing Broker privilege.

Two clocks, deliberately separate:

* The **wait window** the model asks for (``wait_seconds``) is a checkpoint. The Agent waits that
  long by issuing several bounded Broker calls in a row — no model turn in between — and then
  reports back either the result or a running handle. Reaching the end of the window is not a
  failure and never stops the command.
* The **hard limit** is decided by the platform, not the model: the supervisor process inside the
  sandbox kills the command's process group at its own deadline whether or not anyone is still
  polling. Waiting again, or re-attaching after a reconnect, never moves that deadline.
"""

import asyncio
import hashlib
import json
import math
import re
import shlex
import time
from pathlib import Path
from uuid import uuid4

from ..config import settings

# The tool names follow Pi (bash/bash_wait/bash_kill); the older names stay accepted so stored
# checkpoints and callers that speak the internal vocabulary keep working.
ACTIONS = {
    "bash": "start",
    "exec": "start",
    "bash_wait": "wait",
    "exec_wait": "wait",
    "bash_kill": "kill",
    "exec_kill": "kill",
    "exec_kill_all": "kill_all",
}

DEFAULT_WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 300  # one model turn should not block longer than this without a decision
WAIT_CHUNK_SECONDS = 30  # one Broker call; each response is released before the next is issued
CONTROL_GRACE_SECONDS = 15  # the Broker call must outlive the wait it asks the sandbox for

RUNNING_HINT = (
    "等待窗口结束（不是失败，也不是命令超时）：命令仍在运行。有进展就用 bash_wait 继续等；"
    "需要判断时用 bash 查看进程或产物；确认要终止才 bash_kill。长时间没有输出不等于卡死。"
)


def command_handle(run_id: str, invocation: str, command: str) -> str:
    """A handle derived from the call, so a resumed run waits on the same execution instead of starting it again."""
    digest = hashlib.sha256(f"{run_id}\0{invocation}\0{command}".encode()).hexdigest()
    return digest[:32]


def command_text(args: dict) -> str:
    """The command itself; ``cmd`` stays accepted because older calls and checkpoints used it."""
    value = args.get("command", args.get("cmd"))
    return value if isinstance(value, str) else ""


def _wait_seconds(args: dict, action: str) -> tuple[int, str]:
    """The wait window and, when the model sent a retired argument, the sentence that explains it."""
    note = ""
    for stale in ("timeout", "timeout_seconds"):
        if stale in args and not note:
            # It used to mean the wait window and read as a kill deadline. It now means neither, and
            # failing the call only costs the model a turn, so it is dropped with a note instead.
            note = f"{stale} 已忽略：它既不设定等待窗口也不终止命令；本次等待用 wait_seconds，命令期限由平台决定"
    raw = args.get("wait_seconds", args.get("yield_seconds", args.get("seconds", DEFAULT_WAIT_SECONDS)))
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("wait_seconds 须是整数秒")
    low = 1 if action == "start" else 0
    if not low <= raw <= MAX_WAIT_SECONDS:
        raise ValueError(f"wait_seconds 须在 {low} 到 {MAX_WAIT_SECONDS} 秒之间")
    return raw, note


class _BoundedOutput:
    """Head and tail of everything one call has seen, so the model keeps both ends without unbounded growth."""

    HEAD_CHARS = 1000
    TAIL_CHARS = 8000

    def __init__(self) -> None:
        self.head = ""
        self.tail = ""
        self.total = 0

    def add(self, text: str) -> None:
        if not text:
            return
        self.total += len(text)
        if len(self.head) < self.HEAD_CHARS:
            self.head += text[: self.HEAD_CHARS - len(self.head)]
        self.tail = (self.tail + text)[-self.TAIL_CHARS :]

    def text(self) -> str:
        if self.total <= len(self.head):
            return self.head[: self.total]
        tail_start = self.total - len(self.tail)
        if tail_start <= len(self.head):  # head and tail meet or overlap: no gap to elide
            return self.head + self.tail[len(self.head) - tail_start :]
        elided = tail_start - len(self.head)
        return f"{self.head}\n...[elided {elided} chars]...\n{self.tail}"


async def _control(broker, lease_id: str, payload: dict, seconds: int) -> dict:
    """One bounded sandbox round trip: the helper waits at most ``seconds`` for the command and answers."""
    source = Path(__file__).with_name("background_helper.py").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    installed = getattr(broker, "_background_helpers", None)
    if installed is None:
        installed = broker._background_helpers = set()
    helper = f"/tmp/ailab-background/helper-{digest}.py"
    staging = f"/workspace/.ailab-helper-{digest}.py"
    if (lease_id, digest) not in installed:
        await broker.put_file(lease_id, staging, source)
    request = f"/workspace/.ailab-exec-{uuid4().hex}.json"
    await broker.put_file(lease_id, request, json.dumps(payload, ensure_ascii=False).encode())
    # Verify on every invocation, including after Agent restart or sandbox restore.
    # The control payload never competes with Broker's command-length budget.
    program = (
        "import hashlib,os,runpy,sys;from pathlib import Path;"
        f"p=Path({helper!r});s=Path({staging!r});"
        "p.parent.mkdir(mode=0o700,parents=True,exist_ok=True);"
        f"assert hashlib.sha256((s if s.exists() else p).read_bytes()).hexdigest()=={digest!r};"
        # /tmp is a separate tmpfs on real Docker leases; rename from /workspace is EXDEV.
        "t=p.with_name(p.name+'.'+str(os.getpid())+'.tmp');"
        "(t.write_bytes(s.read_bytes()),t.replace(p),s.unlink()) if s.exists() else None;"
        "sys.argv=[str(p),sys.argv[1]];runpy.run_path(str(p),run_name='__main__')"
    )
    command = "python3 -c " + shlex.quote(program) + " " + shlex.quote(request)
    result = await broker.exec(lease_id, command, math.ceil(seconds + CONTROL_GRACE_SECONDS))
    if result.get("exit_code") != 0:
        raise ValueError("后台命令控制失败：" + str(result.get("stderr", ""))[-500:])
    installed.add((lease_id, digest))
    return json.loads(result["stdout"])


def _final(response: dict, action: str, output: _BoundedOutput) -> dict:
    """The command finished, or the request never started one: one shape, whatever the helper answered with."""
    if "exit_code" not in response:
        return response  # capacity limit, lost handle, sandbox refusal
    exit_code = response["exit_code"]
    killed_by = response.get("killed_by")
    stdout, stderr = response.get("stdout") or "", response.get("stderr") or ""
    if not stdout and not stderr:
        stdout = output.text()  # the sandbox lost its own copy: hand back what the waits collected
    final = {
        # An explicit kill succeeded by definition; any other non-zero exit code is the command's failure.
        "ok": exit_code == 0 or (action == "kill" and killed_by == "caller"),
        "exit_code": exit_code,
        "running": False,
        "stdout": stdout,
        "stderr": stderr,
    }
    for key in ("duration_ms", "idle_seconds", "elapsed_seconds", "total_bytes", "truncated", "log_path"):
        if response.get(key) not in (None, ""):
            final[key] = response[key]
    if response.get("lost"):
        # The sandbox has no exit record and the process is gone: no result exists, and the model must
        # check the workspace instead of trusting an exit code -1.
        final["lost"] = True
        final["error"] = response.get("error") or "后台进程退出状态丢失，请先检查文件"
    elif killed_by == "deadline":
        final["killed_by"] = "deadline"
        final["remaining_seconds"] = 0
        final["error"] = "命令超过平台期限，已被终止（这不是等待窗口结束，也不是命令自己退出）"
    elif killed_by == "caller":
        final["killed_by"] = "caller"
        if action != "kill":
            final["error"] = "命令已被终止"
    elif exit_code != 0:
        final["error"] = f"Command exited with code {exit_code}"
    return final


def _running(response: dict, output: _BoundedOutput, window: int, offset: int) -> dict:
    return {
        "ok": True,
        "handle": response["handle"],
        "running": True,
        "exit_code": None,
        # Not stdout: while the command runs, the log holds both streams interleaved.
        "output": output.text(),
        "total_bytes": offset,
        "truncated": bool(response.get("truncated")),
        "idle_seconds": response.get("idle_seconds"),
        "elapsed_seconds": response.get("elapsed_seconds"),
        "remaining_seconds": response.get("remaining_seconds"),
        "wait_seconds": window,
        "hint": RUNNING_HINT,
        **({"log_path": response["log_path"]} if response.get("log_path") else {}),
    }


async def background_exec(name, args, lease_id, broker, *, max_seconds=None, handle=None):
    action = ACTIONS.get(name)
    if action is None:
        raise ValueError(f"unknown background action: {name}")
    note = ""
    if action in {"kill", "kill_all"}:
        window = 0  # one round trip: report what happened, let the model decide to ask again
    elif action == "start" and args.get("background") is True:
        window = 0
    else:
        window, note = _wait_seconds(args, action)
    if action in {"start", "kill_all"}:
        handle = handle or args.get("handle") or uuid4().hex
    else:
        handle = args.get("handle", "")
    if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{32}", handle):
        raise ValueError("后台命令句柄无效")
    offset = args.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("输出位置无效")
    command = command_text(args)
    if action == "start" and not command.strip():
        raise ValueError("后台命令不能为空")
    if action == "start" and len(command) > 20000:
        raise ValueError("命令不能超过20000字符")
    lifetime = settings.run_max_seconds if max_seconds is None else max(0.001, min(max_seconds, settings.run_max_seconds))
    deadline = time.monotonic() + window
    output = _BoundedOutput()
    step = action
    while True:
        remaining_window = max(0.0, deadline - time.monotonic())
        chunk = 0 if step == "kill" else min(WAIT_CHUNK_SECONDS, math.ceil(remaining_window))
        issued = time.monotonic()
        response = await _control(
            broker,
            lease_id,
            {
                "action": step,
                "handle": handle,
                "cmd": command if step == "start" else None,
                "seconds": chunk,
                "offset": offset,
                "max_jobs": settings.exec_background_max_jobs,
                "log_bytes": settings.exec_background_log_bytes,
                "max_seconds": lifetime,
                "expires_at": time.time() + lifetime,
            },
            chunk,
        )
        step = "wait"  # the sandbox keeps the job; any later chunk only waits on it
        if response.get("running") is True and chunk:
            # A sandbox that answered before its window ended must not be polled in a tight loop.
            await asyncio.sleep(min(max(0.0, chunk - (time.monotonic() - issued)), max(0.0, deadline - time.monotonic())))
        if response.get("running") is not True:
            result = _final(response, action, output)
            break
        total = response.get("total_bytes")
        delta = response.get("output_tail") or ""
        if not isinstance(total, int):  # a response without a byte counter: take its delta as new
            output.add(delta)
        elif total > offset:
            # The helper caps one response's delta; say so rather than stitching a gap away. Its read
            # start is exact; the byte count of the decoded tail is only a fallback for an older helper.
            start = response.get("from")
            gap = start - offset if isinstance(start, int) else total - offset - len(delta.encode())
            if gap > 0:
                output.add(f"\n...[elided {gap} bytes]...\n")
            output.add(delta)
            offset = total
        if time.monotonic() >= deadline:
            result = _running(response, output, window, offset)
            break
    if note:
        result["note"] = note
    return result
