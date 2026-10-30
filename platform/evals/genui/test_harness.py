"""Harness regression tests, no paid model and no application database."""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cases import CASES
from score import score

PLATFORM = Path(__file__).resolve().parents[2]


class HarnessTests(unittest.TestCase):
    def test_mock_tool_round_trip_and_limit(self):
        from run import ToolRoundLimit, answer_with_tools

        from ailab_platform.config import settings
        from ailab_platform.llm import LLMResponse, ToolCall

        class Client:
            def __init__(self, endless=False):
                self.calls = 0
                self.endless = endless

            async def chat_stream(self, messages, tools, model):
                self.calls += 1
                assert tools[0]["function"]["name"] == "ui_components" and len(tools) == 1
                if self.calls == 1 or self.endless:
                    return LLMResponse(
                        tool_calls=[ToolCall(str(self.calls), "ui_components", {"names": ["Text"]})], usage={"prompt_tokens": 10}
                    )
                result = json.loads(messages[-1]["content"])
                assert result["ok"] and result["guide"] and "Text(" in result["components"]
                return LLMResponse(content="完成", usage={"prompt_tokens": 20, "completion_tokens": 5})

        old = settings.genui_html_enabled
        try:
            settings.genui_html_enabled = False
            record = {}
            response = asyncio.run(answer_with_tools(Client(), [], "mock", record))
            self.assertEqual(response.content, "完成")
            self.assertEqual(
                record,
                {
                    "tool_called": True,
                    "tool_call_count": 1,
                    "tool_round_count": 1,
                    "component_names": ["Text"],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 5},
                },
            )
            client = Client(endless=True)
            with self.assertRaises(ToolRoundLimit):
                asyncio.run(answer_with_tools(client, [], "mock", record))
            self.assertEqual(client.calls, 4)
            self.assertEqual(record["usage"], {"prompt_tokens": 40})
        finally:
            settings.genui_html_enabled = old

    def test_case_coverage(self):
        self.assertGreaterEqual(len(CASES), 34)
        self.assertEqual(len({c["id"] for c in CASES}), len(CASES))
        self.assertGreaterEqual(sum(c["kind"] == "no_ui" for c in CASES), 8)
        self.assertGreaterEqual(sum(c["kind"] == "html" for c in CASES), 4)
        self.assertGreaterEqual(sum(bool(c["edit"]) for c in CASES), 5)

    def test_checker_rejects_bad_blocks_and_evaluates_independent_controls(self):
        sources = [
            ("valid", '```openui\nroot = Stack([Text("好")])\n```'),
            ("missing-root", '```openui\nother = Stack([Text("好")])\n```'),
            ("unclosed", '```openui\nroot = Stack([Text("好")])'),
            ("unknown", '```openui\nroot = Stack([MadeUp("x")])\n```'),
            ("bad-props", "```openui\nroot = Stack([Metric()])\n```"),
            ("empty-html", '```openui\nroot = Stack([HtmlArtifact("页面", "", "")])\n```'),
            ("namespace-html", '```openui\nroot = Stack([HtmlArtifact("页", \'var NS="http://www.w3.org/2000/svg";\', "摘要")])\n```'),
            (
                "namespace-resource",
                '```openui\nroot = Stack([HtmlArtifact("页", \'<script src="http://www.w3.org/2000/svg"></script>\', "摘要")])\n```',
            ),
            ("network-html", '```openui\nroot = Stack([HtmlArtifact("页面", "fetch(1)", "摘要")])\n```'),
            (
                "decimals",
                '```openui\nroot = Stack([Metric("数",1,{decimals:2}),Table(["列"],[[1]],{decimals:2}),'
                'LineChart("图",["月"],[Series("数",[1])],{decimals:2}),BarChart("图",["月"],[Series("数",[1])],{decimals:2}),'
                'PieChart("图",["项"],[1],{decimals:2})])\n```',
            ),
            ("array-plus", '```openui\nroot = Stack([Table(["列"],rows + [total])])\nrows = [[1]]\ntotal = [1]\n```'),
            ("bad-chart", '```openui\nroot = Stack([LineChart("图",["月"],[Series("数",[1,2])])])\n```'),
            ("bad-pie", '```openui\nroot = Stack([PieChart("图",["项"],[1,2])])\n```'),
            ("short-pie", '```openui\nroot = Stack([PieChart("图",["甲","乙"],[1])])\n```'),
            ("negative-pie", '```openui\nroot = Stack([PieChart("图",["项"],[-1])])\n```'),
            ("nested-pie", '```openui\nroot = Stack([PieChart("图",["项"],@Each([1],"x",[x]))])\n```'),
            ("bad-bar", '```openui\nroot = Stack([BarChart("图",["月"],[Series("数",[1,2])])])\n```'),
            ("bad-opts", '```openui\nroot = Stack([Table(["列"],[[1]],5)])\n```'),
            ("large-block", '```openui\nroot = Stack([Text("' + "x" * 32000 + '")])\n```'),
            ("large-html", '```openui\nroot = Stack([HtmlArtifact("页", "' + "x" * 24001 + '", "摘要")])\n```'),
            ("large-fallback", '```openui\nroot = Stack([HtmlArtifact("页", "页面", "' + "x" * 2001 + '")])\n```'),
            ("expanded-html", '```openui\nroot = Stack([HtmlArtifact("页", "' + "x" * 23000 + '", "摘要")])\n```'),
            ("nested", "````text\n```openui\nroot = Stack([])\n```\n````"),
            (
                "controls",
                '```openui\nroot = Stack([Slider("数量",1,10,$n),Toggle("加价",$t),Metric("费用",$n*2+($t?5:0))])\n$n=2\n$t=false\n```',
            ),
        ]
        with tempfile.TemporaryDirectory(prefix="genui-check-", dir="/tmp") as directory:
            answers = Path(directory) / "answers.jsonl"
            answers.write_text("\n".join(json.dumps(dict(case=id, answer=source, turn=1, kind="test")) for id, source in sources))
            output = subprocess.run(
                ["node", str(Path(__file__).with_name("check.ts").resolve()), str(answers)],
                cwd=PLATFORM / "portal",
                env={"PATH": os.environ["PATH"], "OPENUI_TELEMETRY_DISABLED": "1"},
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        rows = {r["case"]: r for r in map(json.loads, output.stdout.splitlines())}
        self.assertTrue(rows["valid"]["blocks"][0]["valid"])
        self.assertTrue(rows["decimals"]["blocks"][0]["valid"])
        self.assertTrue(rows["expanded-html"]["blocks"][0]["valid"])
        for id in [
            "missing-root",
            "unclosed",
            "unknown",
            "bad-props",
            "empty-html",
            "bad-opts",
            "large-block",
            "large-html",
            "large-fallback",
        ]:
            self.assertFalse(rows[id]["blocks"][0]["valid"], id)
        for id in ["array-plus", "bad-chart", "bad-pie", "short-pie", "negative-pie", "nested-pie", "bad-bar"]:
            block = rows[id]["blocks"][0]
            self.assertTrue(block["valid"], id)
            self.assertFalse(block["displaysCorrectly"], id)
            self.assertEqual(len(block["malformedData"]), 1)
        self.assertFalse(rows["network-html"]["blocks"][0]["safe"])
        self.assertTrue(rows["namespace-html"]["blocks"][0]["safe"])
        self.assertEqual(rows["namespace-html"]["blocks"][0]["html"][0]["violations"], ["http(s) reference"])
        self.assertFalse(rows["namespace-resource"]["blocks"][0]["safe"])
        self.assertEqual(rows["nested"]["blocks"], [])
        probes = rows["controls"]["blocks"][0]["probes"]
        self.assertEqual([p["data"][-1]["props"]["value"] for p in probes], [20, 9])

    def test_failed_semantics_cannot_pass_by_parsing_alone(self):
        answers = [dict(case="edit-pie", turn=2, answer="x", latency_seconds=1, usage={"completion_tokens": 2})]
        checks = [
            dict(case="edit-pie", turn=2, kind="edit", blocks=[dict(valid=True, safe=True, closed=True, chars=20, components=["PieChart"])])
        ]
        result = score(answers, checks, {"edit-pie:2": dict(edit_success=False, data_consistent=False)})
        self.assertEqual(result["closed_block_validity"], 1)
        self.assertEqual(result["edit_success"], 0)
        self.assertEqual(result["failing_cases"], ["edit-pie"])
        missing = score(
            [dict(case="html-pendulum", turn=1, answer="```html\nraw page\n```", latency_seconds=1)],
            [dict(case="html-pendulum", turn=1, kind="html", blocks=[])],
            {},
        )
        self.assertEqual(missing["failing_cases"], ["html-pendulum"])
        tabs = score(
            [dict(case="switch-view", turn=1, answer="x", latency_seconds=1)],
            [
                dict(
                    case="switch-view",
                    turn=1,
                    kind="controls",
                    blocks=[dict(valid=True, safe=True, closed=True, chars=20, components=["Tabs"])],
                )
            ],
            {},
        )
        self.assertEqual(tabs["controls_used"], 1)
        self.assertEqual(tabs["failing_cases"], [])
        html_bug = score(
            [dict(case="html-pendulum", turn=1, answer="x", latency_seconds=1)],
            [
                dict(
                    case="html-pendulum",
                    turn=1,
                    kind="html",
                    blocks=[dict(valid=True, safe=True, closed=True, chars=20, components=["HtmlArtifact"])],
                )
            ],
            {"html-pendulum:1": dict(html_success=False)},
        )
        self.assertEqual(html_bug["failing_cases"], ["html-pendulum"])
        checks[0]["blocks"][0]["displaysCorrectly"] = False
        malformed = score(answers, checks, {"edit-pie:2": dict(edit_success=True, data_consistent=True, numeric_correct=True)})
        self.assertEqual(malformed["malformed_evaluated_blocks"], 1)
        self.assertEqual(malformed["data_consistent"], 0)
        self.assertEqual(malformed["edit_success"], 0)
        self.assertEqual(malformed["failing_cases"], ["edit-pie"])
        baseline = score(
            [dict(case="edit-pie", turn=1, answer="x", latency_seconds=1)],
            [dict(case="edit-pie", turn=1, kind="edit", blocks=[])],
            {"edit-pie:1": dict(data_consistent=True)},
        )
        self.assertEqual(baseline["data_reviewed"], 0)


if __name__ == "__main__":
    unittest.main()
