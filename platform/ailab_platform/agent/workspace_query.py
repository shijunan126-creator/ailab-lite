"""Workspace queries that must run inside the sandbox: ``ls``, ``grep`` and ``find``.

Pi runs these against a local filesystem; a sandbox's files are only reachable by
executing something there. Each tool is therefore one small Python program passed
to the Broker's synchronous exec, which returns a bounded JSON document. The
programs do the bounding (match limit, 50KB cap, 500-character lines) and stop
the child process as soon as a limit is reached, so neither the sandbox nor the
Broker carries an unbounded result.

They are ordinary scripts: the tests run them with the current interpreter
against a temporary directory, with fake ``rg``/``fd`` executables on PATH, so
their logic is covered without a container.
"""

from __future__ import annotations

import json
import shlex

from ..domain.text_truncate import DEFAULT_MAX_BYTES, GREP_MAX_LINE_LENGTH, format_size, truncate_head

LS_LIMIT = 500
GREP_DEFAULT_LIMIT = 100
FIND_DEFAULT_LIMIT = 1000

# Read-only queries answer the model synchronously; a tree walk that takes longer
# than this fails instead of handing back a background handle the model must poll.
QUERY_TIMEOUT_SECONDS = 60

NO_RIPGREP = "sandbox image has no ripgrep (rg)"
NO_FD = "sandbox image has no fd"

LS_PROGRAM = r"""
import json, os, stat, sys

request = json.loads(sys.argv[1])
root, limit = request["path"], max(1, int(request["limit"]))
try:
    if not os.path.exists(root):
        raise ValueError("Path not found: " + root)
    if os.path.islink(root) or not os.path.isdir(root):
        raise ValueError("Not a directory: " + root)
    try:
        names = sorted(os.listdir(root), key=lambda name: (name.lower(), name))
    except OSError as exc:
        raise ValueError("Cannot read directory: " + str(exc))
    entries, limit_reached = [], False
    for name in names:
        if len(entries) >= limit:
            limit_reached = True
            break
        try:
            info = os.stat(os.path.join(root, name))
        except OSError:
            continue
        entries.append(name + ("/" if stat.S_ISDIR(info.st_mode) else ""))
    print(json.dumps({"ok": True, "entries": entries, "limit_reached": limit_reached}))
except (OSError, ValueError) as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
"""

GREP_PROGRAM = r"""
import base64, json, os, shutil, subprocess, sys, tempfile

MAX_BYTES = int(sys.argv[2])
MAX_LINE = int(sys.argv[3])
request = json.loads(sys.argv[1])
root, limit = request["path"], max(1, int(request["limit"]))

def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(0)

def field(value):
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("bytes"), str):
            return base64.b64decode(value["bytes"]).decode("utf-8", "replace")
    return ""

def strip_terminator(text):
    if text.endswith("\n"):
        text = text[:-1]
    if text.endswith("\r"):
        text = text[:-1]
    return text

rg = shutil.which("rg")
if not rg:
    fail("sandbox image has no ripgrep (rg)")
if not os.path.exists(root):
    fail("Path not found: " + root)
is_directory = os.path.isdir(root)
args = [rg, "--no-config", "--json", "--line-number", "--color=never", "--hidden"]
if request.get("ignore_case"):
    args.append("--ignore-case")
if request.get("literal"):
    args.append("--fixed-strings")
glob = request.get("glob") or ""
if glob:
    args.extend(["--glob", glob.lstrip("./")])
context = max(0, int(request.get("context") or 0))
if context:
    args.extend(["--context", str(context)])
args.extend(["--", request["pattern"], "." if is_directory else root])

lines, matches, output_bytes = [], 0, 0
limit_hit = limit_reached = bytes_truncated = lines_truncated = False
trailing_end, last_path = 0, None
stderr = tempfile.TemporaryFile()

def keep(path, number, body, is_match):
    global output_bytes, bytes_truncated, lines_truncated
    entry = "%s:%d: %s" % (path, number, body) if is_match else "%s-%d- %s" % (path, number, body)
    text, cut = entry, False
    if len(entry) > MAX_LINE:
        text, cut = entry[:MAX_LINE] + "... [truncated]", True
    if output_bytes + len(text.encode()) + 1 > MAX_BYTES:
        bytes_truncated = True
        return False
    if cut:
        lines_truncated = True
    lines.append(text)
    output_bytes += len(text.encode()) + 1
    return True

def stop(process):
    if process.poll() is None:
        process.kill()

try:
    process = subprocess.Popen(args, cwd=root if is_directory else None, stdout=subprocess.PIPE, stderr=stderr)
except OSError as exc:
    fail("Failed to run ripgrep: " + str(exc))

for raw in process.stdout:
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except ValueError:
        continue
    kind = event.get("type")
    if kind not in ("match", "context"):
        continue
    data = event.get("data") or {}
    path, number = field(data.get("path")), data.get("line_number")
    if not path or not isinstance(number, int):
        continue
    if path.startswith("./"):
        path = path[2:]
    body = strip_terminator(field(data.get("lines")))
    if not limit_hit:
        if not keep(path, number, body, kind == "match"):
            break
        if kind == "match":
            matches += 1
            last_path = path
            if matches >= limit:
                limit_hit = True
                trailing_end = number + context
        continue
    in_window = path == last_path and number <= trailing_end
    if in_window:
        keep(path, number, body, False)
    if kind == "match":
        limit_reached = True
    if limit_reached and not (in_window and number < trailing_end):
        break
stop(process)
process.wait()
stderr.seek(0)
errors = stderr.read().decode("utf-8", "replace").strip()
stderr.close()
if not lines and process.returncode not in (0, 1) and not limit_reached:
    fail(errors.splitlines()[-1] if errors else "ripgrep exited with code %d" % process.returncode)
print(json.dumps({"ok": True, "lines": lines, "matches": matches, "limit_reached": limit_reached,
                  "bytes_truncated": bytes_truncated, "lines_truncated": lines_truncated}))
"""

FIND_PROGRAM = r"""
import json, os, shutil, subprocess, sys, tempfile

MAX_BYTES = int(sys.argv[2])
request = json.loads(sys.argv[1])
root, limit = request["path"], max(1, int(request["limit"]))

def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(0)

binary = shutil.which("fd") or shutil.which("fdfind")
if not binary:
    fail("sandbox image has no fd")
if not os.path.exists(root):
    fail("Path not found: " + root)
root_absolute = os.path.abspath(root)
inside_git = False
current = root_absolute
while True:
    if os.path.exists(os.path.join(current, ".git")):
        inside_git = True
        break
    parent = os.path.dirname(current)
    if parent == current:
        break
    current = parent
pattern = request["pattern"]
args = [binary, "--color=never", "--hidden"]
if not inside_git:
    args.append("--no-require-git")
args.extend(["--glob", "--max-results", str(limit + 1)])
is_path_pattern = "/" in pattern
if is_path_pattern and not pattern.startswith("/") and not pattern.startswith("**/") and pattern != "**":
    pattern = "**/" + pattern
if is_path_pattern:
    args.append("--full-path")
args.extend(["--", pattern, root])

def relative(path):
    absolute = os.path.abspath(path)
    if absolute != root_absolute and absolute.startswith(root_absolute + os.sep):
        return os.path.relpath(absolute, root_absolute).replace(os.sep, "/")
    return path.replace(os.sep, "/")

def stop(process):
    if process.poll() is None:
        process.kill()

stderr = tempfile.TemporaryFile()
try:
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr)
except OSError as exc:
    fail("Failed to run fd: " + str(exc))
lines, count, output_bytes = [], 0, 0
limit_reached = bytes_truncated = False
for raw in process.stdout:
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        continue
    if count == limit:
        limit_reached = True
        break
    entry = relative(line)
    count += 1
    if output_bytes + len(entry.encode()) + 1 > MAX_BYTES:
        bytes_truncated = True
        break
    lines.append(entry)
    output_bytes += len(entry.encode()) + 1
stop(process)
process.wait()
stderr.seek(0)
errors = stderr.read().decode("utf-8", "replace").strip()
stderr.close()
if count == 0 and process.returncode not in (0, 1):
    fail(errors.splitlines()[-1] if errors else "fd exited with code %d" % process.returncode)
print(json.dumps({"ok": True, "lines": lines, "limit_reached": limit_reached, "bytes_truncated": bytes_truncated}))
"""


def _command(program: str, args: list) -> str:
    return " ".join([shlex.quote("python3"), "-c", shlex.quote(program), *[shlex.quote(str(arg)) for arg in args]])


async def _run(broker, lease_id: str, command: str) -> dict:
    result = await broker.exec(lease_id, command, QUERY_TIMEOUT_SECONDS)
    if result.get("exit_code") != 0:
        detail = (result.get("stderr") or "").strip().splitlines()
        raise QueryError(detail[-1] if detail else f"沙箱查询失败（exit {result.get('exit_code')}）")
    try:
        payload = json.loads(result.get("stdout") or "")
    except ValueError as exc:
        raise QueryError("沙箱查询没有返回可用的结果") from exc
    if not payload.get("ok"):
        error = str(payload.get("error") or "")
        if error in {NO_RIPGREP, NO_FD}:
            raise QueryError("沙箱镜像缺少搜索工具：" + error.rsplit("(", 1)[-1].rstrip(")"))
        raise QueryError(error or "沙箱查询失败")
    return payload


class QueryError(RuntimeError):
    """The sandbox could not answer; the message is what the model reads."""


def _limit(limit, default: int) -> int:
    return max(1, limit) if isinstance(limit, int) and not isinstance(limit, bool) else default


async def list_directory(broker, lease_id: str, path: str, limit: int | None = None) -> str:
    """A one-level listing, directories marked with ``/``, as Pi's ``ls`` renders it."""
    effective = _limit(limit, LS_LIMIT)
    payload = await _run(broker, lease_id, _command(LS_PROGRAM, [json.dumps({"path": path, "limit": effective})]))
    entries = payload.get("entries") or []
    if not entries:
        return "(empty directory)"
    truncation = truncate_head("\n".join(entries), max_lines=2**62)
    output = truncation.content
    notices = []
    if payload.get("limit_reached"):
        notices.append(f"{effective} entries limit reached. Use limit={effective * 2} for more")
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output


async def search_text(
    broker,
    lease_id: str,
    pattern: str,
    path: str,
    *,
    glob: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    context: int = 0,
    limit: int | None = None,
) -> str:
    """Matching lines with file paths and line numbers, parsed from ``rg --json``."""
    effective = _limit(limit, GREP_DEFAULT_LIMIT)
    context = context if isinstance(context, int) and not isinstance(context, bool) else 0
    request = {
        "pattern": pattern,
        "path": path,
        "glob": glob or "",
        "ignore_case": bool(ignore_case),
        "literal": bool(literal),
        "context": max(0, context),
        "limit": effective,
    }
    payload = await _run(broker, lease_id, _command(GREP_PROGRAM, [json.dumps(request), DEFAULT_MAX_BYTES, GREP_MAX_LINE_LENGTH]))
    lines = payload.get("lines") or []
    if not lines:
        return "No matches found"
    output = "\n".join(lines)
    notices = []
    if payload.get("limit_reached"):
        notices.append(f"{effective} matches limit reached. Use limit={effective * 2} for more, or refine pattern")
    if payload.get("bytes_truncated"):
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if payload.get("lines_truncated"):
        notices.append(f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. Use read tool to see full lines")
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output


async def find_files(broker, lease_id: str, pattern: str, path: str, limit: int | None = None) -> str:
    """Paths matching a glob, relative to the search root, from ``fd``."""
    effective = _limit(limit, FIND_DEFAULT_LIMIT)
    payload = await _run(
        broker,
        lease_id,
        _command(FIND_PROGRAM, [json.dumps({"pattern": pattern, "path": path, "limit": effective}), DEFAULT_MAX_BYTES]),
    )
    lines = payload.get("lines") or []
    if not lines:
        return "No files found matching pattern"
    output = "\n".join(lines)
    notices = []
    if payload.get("limit_reached"):
        notices.append(f"{effective} results limit reached. Use limit={effective * 2} for more, or refine pattern")
    if payload.get("bytes_truncated"):
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output
