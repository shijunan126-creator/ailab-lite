// @vitest-environment jsdom
// Catalog v2: components compute what they show from raw data, and copy says the same thing.
import assert from "node:assert/strict";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import Answer from "../components/chat/answer";
import { answerForCopy } from "../components/genui/markdown-bridge";
import { facts, funnel, histogram, ranking, waterfall, winners, box, lineDiff } from "../components/genui/catalog/data";
import { niceScale } from "../components/genui/catalog/kit";
import { toProgram } from "../components/genui/catalog/program";

vi.mock("../components/genui/telemetry", () => ({ report: () => undefined }));
afterEach(cleanup);

const block = (...lines: string[]) => ["```openui", ...lines, "```"].join("\n");

test("derived numbers: facts, ranks, winners, bridges, funnels, bins and quartiles", () => {
  const sales = facts(["1月", "2月", "3月", "4月", "5月", "6月"], [120, 135, 128, 160, 172, 190], ["change", "rise", "drop", "max", "share", "nope"]);
  assert.deepEqual(sales.map((f) => [f.key, f.at, Math.round(f.value * 10) / 10]), [["change", "1月 → 6月", 70], ["rise", "4月", 32], ["drop", "3月", -7], ["max", "6月", 190], ["share", "6月", 21]]);
  assert.equal(Math.round(sales[0].rate! * 10) / 10, 58.3);
  assert.deepEqual(ranking([{ name: "a", value: 2 }, { name: "b", value: 9 }, { name: "c", value: 9 }], "high").map((r) => [r.name, r.rank]), [["b", 1], ["c", 1], ["a", 3]]);
  assert.deepEqual([...winners([24, 23.75, "n/a"], "low")], [1]);
  assert.equal(winners([5, 5], "high").size, 0); // a tie everywhere marks nothing
  assert.deepEqual(waterfall(["销量", "成本"], [120, -30], 800, "去年", "今年").map((s) => [s.label, s.from, s.to]), [["去年", 0, 800], ["销量", 800, 920], ["成本", 920, 890], ["今年", 0, 890]]);
  assert.deepEqual(funnel([1000, 200, 50]).map((s) => [s.step, s.overall]), [[null, 100], [20, 20], [25, 5]]);
  assert.equal(histogram([1, 2, 2, 3, 9]).reduce((a, b) => a + b.n, 0), 5);
  assert.deepEqual(box([1, 2, 3, 4, 100])!.outliers, [100]);
  assert.deepEqual(lineDiff("a\nb\nc", "a\nc\nd").map((l) => l.kind), ["same", "del", "same", "add"]);
  // a changed line reads old then new
  assert.deepEqual(lineDiff("t = 30\nr = 3", "t = 60\nr = 3").map((l) => l.kind), ["del", "add", "same"]);
  assert.ok(niceScale(0, 10, 4, true).ticks.every(Number.isInteger));
  assert.deepEqual(facts(["a", "b"], [1, 3], ["sum", "Mean", "nope"]).map((f) => [f.key, f.value]), [["total", 4], ["avg", 2]]);
});

test("Highlights, Metric previous and Ranking show what they computed; copy says the same", async () => {
  const source = block(
    "root = Stack([m, h, r])",
    'months = ["1月", "2月", "3月"]',
    "sales = [120, 135, 160]",
    'm = Metric("3 月销售额", 160, {unit: "万元", previous: 135})',
    'h = Highlights(months, sales, ["max", "rise"], {unit: "万元"})',
    'r = Ranking("门店", [{name: "东店", value: 63}, {name: "南店", value: 97}, {name: "北店", value: 84}], {unit: "万元"})',
  );
  const { container } = render(<Answer text={source} />);
  await screen.findByText("3 月销售额");
  const shown = container.textContent || "";
  assert.ok(shown.includes("+18.5%"), shown); // (160 - 135) / 135
  assert.ok(shown.includes("最大增幅") && shown.includes("+25 万元"), shown);
  assert.deepEqual([...container.querySelectorAll(".gu-rank-name")].map((n) => n.textContent), ["南店", "北店", "东店"]);
  const copy = answerForCopy(source);
  assert.ok(copy.includes("- 3 月销售额：160 万元（+18.5%）"), copy);
  assert.ok(copy.includes("最高（3月）：160 万元") && copy.includes("最大增幅（3月）：+25 万元（+18.5%）"), copy);
  assert.ok(copy.includes("1. 南店：97 万元\n2. 北店：84 万元\n3. 东店：63 万元"), copy);
});

test("Compare marks each row's best by its direction and the recommended option", async () => {
  const source = block(
    "root = Stack([c])",
    'c = Compare(["甲", "乙"], [{label: "价格", values: [24, 23.75], better: "low", unit: "元"}, {label: "本地部署", values: [true, false]}], {recommend: "乙"})',
  );
  const { container } = render(<Answer text={source} />);
  await screen.findByText("价格");
  const best = [...container.querySelectorAll("td.gu-best")].map((td) => td.textContent);
  assert.deepEqual(best, ["23.75（最优）"]);
  assert.ok(answerForCopy(source).includes("| 本地部署 | ✓ | ✗ |"));
  assert.ok(answerForCopy(source).includes("推荐：乙"));
});

test("opts are named: a bad key is dropped, the component still renders; positional extras do not shift data", async () => {
  const source = block(
    "root = Stack([b])",
    'b = BarChart("差值", ["A", "B"], [Series("差", [3, -2])], {horizontal: true, unit: 5, decimals: 1})',
  );
  const { container } = render(<Answer text={source} />);
  await waitFor(() => assert.ok(container.querySelector("svg rect")));
  assert.equal(container.querySelector(".gu-note"), null);
  assert.ok((container.textContent || "").includes("-2.0"));
});

test("an unknown component drops alone; the lazy charts and diagrams render", async () => {
  const source = block(
    "root = Stack([x, t, s, f])",
    'x = FooChart("a")',
    't = Text("其余照常")',
    's = Sankey("流向", [{from: "搜索", to: "注册", value: 30}, {from: "搜索", to: "离开", value: 70}])',
    'f = Flow([{id: "a", label: "开始"}, {id: "b", label: "结束"}], [{from: "a", to: "b"}])',
  );
  const { container } = render(<Answer text={source} />);
  await screen.findByText("其余照常");
  await waitFor(() => assert.ok([...container.querySelectorAll(".gu-flow-node")].some((t) => t.textContent === "开始")));
  await waitFor(() => assert.ok([...container.querySelectorAll("text")].some((t) => t.textContent === "注册")));
  assert.ok(answerForCopy(source).includes("| 搜索 | 注册 | 30 |"));
  assert.ok(answerForCopy(source).includes("- 开始 → 结束"), answerForCopy(source));
});

test("data of the wrong shape shows as malformed for the new components too", async () => {
  const source = block(
    "root = Stack([tl, w, ok])",
    'names = ["甲", "乙"]',
    'rows = [{a: 1}]',
    'tl = Timeline(@Each(names, "n", n))',
    'w = Waterfall("桥", ["a"], @Each(rows, "r", r))',
    'ok = Text("其余照常")',
  );
  render(<Answer text={source} />);
  await screen.findByText("其余照常");
  assert.equal(screen.getAllByText("数据格式有误，无法显示").length, 2);
});

test("quotes inside prose stay in the string; escaped quotes and code are untouched; any component can be the root", async () => {
  assert.equal(toProgram('c = Callout("更像"积压订单"所致", {tone: "info"})'), 'c = Callout("更像\\"积压订单\\"所致", {tone: "info"})');
  for (const same of ['c = Code("print(\\"hi\\")")', 't = Text("a" + b, {tone: "muted"})']) assert.equal(toProgram(same), same);
  const source = block(
    "root = Ranking(\"门店\", items, {unit: \"万元\"})",
    'items = [{name: "东店", value: 84}, {name: "南店", value: 97}]',
  );
  const { container } = render(<Answer text={source} />);
  await waitFor(() => assert.deepEqual([...container.querySelectorAll(".gu-rank-name")].map((n) => n.textContent), ["南店", "东店"]));
  cleanup();
  const quoted = block("root = Stack([c, h])", 'c = Callout("这更像"积压订单"，不是需求下滑", {tone: "info"})', 'income = [12000, 18000]', 'h = Highlights(["1月", "2月"], @Each(income, "v", v / 10000), ["max"], {unit: "万元"})');
  const view = render(<Answer text={quoted} />);
  await screen.findByText("这更像\"积压订单\"，不是需求下滑");
  await waitFor(() => assert.ok((view.container.textContent || "").includes("1.8 万元"), view.container.textContent || ""));
});
