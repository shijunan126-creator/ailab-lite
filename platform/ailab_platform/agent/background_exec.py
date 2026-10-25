"""Agent-side bounded jobs over ordinary Broker exec; no runtime socket or Broker privilege.

The helper runs inside a Linux sandbox. Its control files live outside the snapshotted
workspace: a restored workspace cannot accidentally signal a recycled process ID.
"""

import base64
import json
import re
import shlex
from pathlib import Path
from uuid import uuid4

from ..config import settings


async def background_exec(name, args, lease_id, broker):
    handle = uuid4().hex if name == "exec" else args.get("handle", "")
    if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{32}", handle):
        raise ValueError("后台命令句柄无效")
    seconds = args.get("seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 600:
        raise ValueError("等待时间须在 0 到 600 秒之间")
    offset = args.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("输出位置无效")
    command_text = args.get("cmd") or args.get("command")
    if name == "exec" and (not isinstance(command_text, str) or not command_text.strip()):
        raise ValueError("后台命令不能为空")
    payload = {
        "action": "start" if name == "exec" else "wait" if name == "exec_wait" else "kill",
        "handle": handle,
        "cmd": command_text,
        "seconds": seconds,
        "offset": offset,
        "max_jobs": settings.exec_background_max_jobs,
        "log_bytes": settings.exec_background_log_bytes,
        "max_seconds": settings.run_max_seconds,
    }
    source = Path(__file__).with_name("background_helper.py").read_bytes()
    encoded = base64.b64encode(source).decode()
    program = f"import base64;HELPER_SOURCE=base64.b64decode({encoded!r}).decode();exec(HELPER_SOURCE)"
    command = "python3 -c " + shlex.quote(program) + " " + shlex.quote(json.dumps(payload))
    if len(command) > 20000:
        raise ValueError("后台命令过长，请先写入脚本文件再执行")
    result = await broker.exec(lease_id, command, min(600, seconds + 10) if payload["action"] == "wait" else 30)
    if result.get("exit_code") != 0:
        raise ValueError("后台命令控制失败：" + str(result.get("stderr", ""))[-500:])
    return json.loads(result["stdout"])
