"""One bounded exec mechanism over the existing Broker privilege."""

import hashlib
import json
import math
import re
import shlex
import time
from pathlib import Path
from uuid import uuid4

from ..config import settings


async def background_exec(name, args, lease_id, broker, *, max_seconds=None):
    handle = uuid4().hex if name in {"exec", "exec_kill_all"} else args.get("handle", "")
    if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{32}", handle):
        raise ValueError("后台命令句柄无效")
    if name == "exec":
        seconds = args.get("yield_seconds", args.get("timeout_seconds", 30))
        if args.get("background") is True:
            seconds = 0
        elif isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 120:
            raise ValueError("yield_seconds 须在 1 到 120 秒之间")
    else:
        seconds = args.get("seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 300:
        raise ValueError("等待时间须在 0 到 300 秒之间")
    offset = args.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("输出位置无效")
    command_text = args.get("cmd") or args.get("command")
    if name == "exec" and (not isinstance(command_text, str) or not command_text.strip()):
        raise ValueError("后台命令不能为空")
    if name == "exec" and len(command_text) > 20000:
        raise ValueError("命令不能超过20000字符")
    lifetime = settings.run_max_seconds if max_seconds is None else max(0.001, min(max_seconds, settings.run_max_seconds))
    payload = {
        "action": {"exec": "start", "exec_wait": "wait", "exec_kill": "kill", "exec_kill_all": "kill_all"}[name],
        "handle": handle,
        "cmd": command_text,
        "seconds": min(seconds, lifetime) if name == "exec" else seconds,
        "offset": offset,
        "max_jobs": settings.exec_background_max_jobs,
        "log_bytes": settings.exec_background_log_bytes,
        "max_seconds": lifetime,
        "expires_at": time.time() + lifetime,
    }
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
    result = await broker.exec(lease_id, command, math.ceil(seconds + 10))
    if result.get("exit_code") != 0:
        raise ValueError("后台命令控制失败：" + str(result.get("stderr", ""))[-500:])
    installed.add((lease_id, digest))
    response = json.loads(result["stdout"])
    if name == "exec" and response.get("exit_code") is not None:
        return {
            "ok": response["exit_code"] == 0,
            "exit_code": response["exit_code"],
            "stdout": response.pop("stdout", ""),
            "stderr": response.pop("stderr", ""),
            "duration_ms": response.get("duration_ms"),
        }
    return response
