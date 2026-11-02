"""Bounded office resource reads and visibility derived solely from paired messages."""

import hashlib
import json
from pathlib import PurePosixPath

from ..skill_registry import BASE_SKILLS, catalog, resolve_name

SUMMARY_MARKER = "\n已读模块索引（仅供导航，正文须按需重读）："


def read_module(name, mode, resource=None):
    entry = catalog().get(name)
    if entry is None:
        raise ValueError("unknown skill")
    root = entry["path"].parent
    manifest = json.loads((root / "modules.json").read_text())
    modules = [m for m in manifest["modules"] if mode in m["modes"]]
    common = {"ok": True, "name": name, "version": entry["version"], "package_hash": entry["sha256"], "mode": mode}
    if mode == "work":
        common["base_path"] = f"/opt/skills/{name}"
    if resource is None:
        text = entry["path"].read_text()
        if mode == "chat":
            import re

            text = re.sub(r"<!-- work -->.*?<!-- /work -->", "", text, flags=re.DOTALL)
        if mode == "chat":
            text += "\n当前对话不能运行脚本、创建或交付文件。"
        return {**common, "skill": text, "navigation": True, "hash": hashlib.sha256(text.encode()).hexdigest()}
    if not isinstance(resource, str) or PurePosixPath(resource).is_absolute() or ".." in PurePosixPath(resource).parts:
        raise ValueError("invalid resource")
    module = next((m for m in modules if m["path"] == resource), None)
    if module is None:
        raise ValueError("unknown or unavailable resource for this mode")
    path = root / resource
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("invalid resource path")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != module["sha256"]:
        raise ValueError("resource hash mismatch; rebuild context with the installed package")
    return {
        **common,
        "resource": resource,
        "hash": module["sha256"],
        "module_hash": module["sha256"],
        "estimated_tokens": module["estimated_tokens"],
        "deps": module["deps"],
        "complete": True,
        "skill": data.decode(),
    }


def identity(result):
    return tuple(result.get(k) for k in ("name", "resource", "hash", "version", "package_hash", "mode"))


def visible_modules(messages, *, calls=None, visible=None):
    calls = {} if calls is None else calls
    visible = {} if visible is None else visible
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") == "load_skill":
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                        if isinstance(args, dict):
                            calls[call.get("id")] = args
                    except ValueError:
                        pass
        elif message.get("role") == "tool" and message.get("name") == "load_skill" and message.get("tool_call_id") in calls:
            try:
                result = json.loads(message.get("content") or "{}")
            except ValueError:
                continue
            args = calls.pop(message["tool_call_id"])
            if not isinstance(result, dict) or not result.get("ok") or not result.get("skill"):
                continue
            if result.get("name") not in BASE_SKILLS or resolve_name(args.get("name", "")) != result["name"]:
                continue
            if args.get("resource") != result.get("resource"):
                continue
            if not (result.get("navigation") or result.get("complete")):
                continue
            if hashlib.sha256(result["skill"].encode()).hexdigest() == result.get("hash"):
                visible[identity(result)] = {k: v for k, v in result.items() if k != "skill"}
    return visible


def module_summary(messages):
    entries = {(r["name"], r["resource"], r["hash"]) for r in visible_modules(messages).values() if r.get("resource")}
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str) and SUMMARY_MARKER in content:
            try:
                entries.update(tuple(item) for item in json.loads(content.split(SUMMARY_MARKER)[-1]))
            except (ValueError, TypeError):
                pass
        elif message.get("role") == "system" and isinstance(content, str) and "历史摘要（仅作背景）：\n" in content:
            try:
                parsed = json.loads(content.split("历史摘要（仅作背景）：\n", 1)[1])
                entries.update(tuple(item[k] for k in ("name", "resource", "hash")) for item in parsed.get("read_modules", []))
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
    return SUMMARY_MARKER + json.dumps(sorted(entries), ensure_ascii=False) if entries else ""


def annotate_summary(summary, messages, *, structured=False):
    note = module_summary(messages)
    if not structured:
        return summary + note
    parsed = json.loads(summary)
    keys = ("name", "resource", "hash")
    entries = json.loads(note.split(SUMMARY_MARKER)[1]) if note else []
    parsed["read_modules"] = [dict(zip(keys, item, strict=True)) for item in entries]
    return json.dumps(parsed, ensure_ascii=False)


class ModuleVisibility:
    """An ephemeral incremental index; compaction/recovery rebuild it from messages."""

    def __init__(self):
        self.messages = None
        self.count = 0
        self.last = None
        self.calls = {}
        self.visible = {}

    def read(self, messages):
        if self.messages is not messages or len(messages) < self.count or (self.count and messages[self.count - 1] is not self.last):
            self.calls, self.visible, self.count = {}, {}, 0
        visible_modules(messages[self.count :], calls=self.calls, visible=self.visible)
        self.messages, self.count = messages, len(messages)
        self.last = messages[-1] if messages else None
        return self.visible
