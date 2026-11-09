"""Real-model own-write and external document edits, using AgentLoop with service doubles."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

from ailab_platform.agent.loop import AgentLoop
from scripts.evaluate_memory_prefix import Platform, Recorder, cache_acceptance, scenarios
from tests.test_agent_loop import FakeBroker


class External(Platform):
    def __init__(self, mode, model):
        initial = copy.deepcopy(scenarios()["unchanged"][0])
        initial["entries"].append(
            {"id": "mem_page", "kind": "fact", "origin": "manual", "content": "页面原内容", "observed_at": "2026-01-01"}
        )
        self.live = copy.deepcopy(initial)
        self.own = False
        super().__init__(mode, model, "external-and-own", [initial] * 4)

    async def get_memory_context(self, run_id):
        return copy.deepcopy(self.live)

    async def memory(self, run_id, args):
        if args.get("action") == "list":
            from ailab_platform.memory_recall.document import render

            return {
                "memory_list": True,
                "revision": self.live["revision"],
                "entries": copy.deepcopy(self.live["entries"]),
                "document": render(self.live["entries"]),
            }
        if args.get("action") == "add":
            from ailab_platform.memory_recall.document import write_result

            before = copy.deepcopy(self.live)
            self.own = True
            self.live["revision"] += 1
            self.live["entries"].append(
                {
                    "id": "mem_own",
                    "kind": "preference",
                    "origin": "explicit",
                    "source": "agent",
                    "evidence_run_ids": [run_id],
                    "content": args["content"],
                    "observed_at": "2026-11-09",
                }
            )
            return write_result(before, self.live)
        raise AssertionError("unexpected memory action")

    async def get_run(self, run_id):
        if run_id == "turn1":
            self.live["revision"] += 3
            self.live["entries"] = [e for e in self.live["entries"] if e["id"] != "mem_core"]
            for e in self.live["entries"]:
                if e["id"] == "mem_project":
                    e["content"] = "Atlas已进入验收"
                if e["id"] == "mem_page":
                    e["content"] = "页面修改内容"
            self.live["entries"].append(
                {"id": "mem_external", "kind": "fact", "content": "外部会话新增", "origin": "chat", "observed_at": "2026-11-09"}
            )
        run = await super().get_run(run_id)
        if run_id != "turn0":
            run["previous_model_context"]["finished_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            previous = self.checkpoints[f"turn{int(run_id[-1]) - 1}"]["state"]
            run["previous_model_context"]["memory_snapshot_signature"] = previous.get("memory_snapshot_signature")
        if run_id == "turn1":
            run["prompt"] = "请用一句话解释什么是哈希表。"
        if run_id == "turn2":
            run["prompt"] = "请用一句话解释哈希表查找为什么快。"
        if run_id == "turn0":
            run["prompt"] = "请调用memory工具记住我偏好用表格比较选项，成功后仅回复已记住。"
        return run


async def evaluate(model):
    out = {}
    for mode in ("chat", "work"):
        p = External(mode, model)
        rec = Recorder(model)
        loop = AgentLoop(platform=p, broker=FakeBroker(), llm=rec)
        try:
            for i in range(4):
                await loop.process(f"turn{i}", attempt_id=f"a{i}")
            terminal = [status for status, _ in p.statuses if status in {"succeeded", "failed", "canceled"}]
            assert terminal == ["succeeded"] * 4, terminal
            for left, right in zip(rec.requests, rec.requests[1:]):
                assert left.get("tools") == right.get("tools")
                assert right["messages"][: len(left["messages"])] == left["messages"]
            updates = [
                m
                for m in rec.requests[-1]["messages"]
                if m.get("role") == "user" and str(m.get("content", "")).startswith("<memory_update>")
            ]
            assert p.own and len(updates) == 1, (p.own, len(updates))
            update = updates[0]["content"]
            assert "mem_own" not in update and all(
                x in update for x in ("已删除，不得再使用：[mem_core]", "Atlas已进入验收", "页面修改内容")
            )
            assert "外部会话新增" not in update
            assert all(r["messages"][:2] == rec.requests[0]["messages"][:2] for r in rec.requests)
            out[mode] = {
                "acceptance": cache_acceptance(rec.usages, 1),
                "calls": rec.usages,
                "own_write": True,
                "external_update_count": len(updates),
                "resume_idle_hours": 2,
                "prefix_extensions": True,
            }
        finally:
            rec.close()
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    result = asyncio.run(evaluate(parser.parse_args().model))
    print(json.dumps(result, indent=2))
    raise SystemExit(not all(case["acceptance"]["passed"] for case in result.values()))
