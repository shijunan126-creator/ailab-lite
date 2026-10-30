// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import Answer from "../components/chat/answer";
import { GenUIHostProvider } from "../components/genui/host";
import { answerForCopy } from "../components/genui/markdown-bridge";

vi.mock("../components/genui/telemetry", () => ({ report: () => undefined }));
afterEach(cleanup);

const metrics = '```openui\nroot = Stack([g])\ng = Grid([m1, m2])\nm1 = Metric("GPT", 320, {unit: \"次\"})\nm2 = Metric("Claude", 210, {unit: \"次\"})\n```';

test("text and a UI block render in order; numbers are formatted for zh-CN", async () => {
  const { container } = render(<Answer text={`最高的是 GPT：\n\n${metrics}\n\n占一半。`} />);
  await screen.findByText("GPT");
  const text = container.textContent || "";
  assert.ok(text.indexOf("最高的是") < text.indexOf("GPT") && text.indexOf("Claude") < text.indexOf("占一半"));
  assert.ok(text.includes("320") && text.includes("210"));
});

test("a stray fence after a UI block adds no empty code box", async () => {
  const { container } = render(<Answer text={`${metrics}\n\`\`\`\n`} />);
  await screen.findByText("GPT");
  assert.equal(container.querySelector(".md-codeblock"), null);
  const { container: real } = render(<Answer text={"\`\`\`js\nlet a = 1\n\`\`\`"} />);
  assert.ok(real.querySelector(".md-codeblock"));
});

test("a block still streaming shows the components that have arrived, before its fence closes", async () => {
  const partial = '看下面：\n```openui\nroot = Stack([m1, m2])\nm1 = Metric("GPT", 320, {unit: \"次\"})\nm2 = Metric("Cla';
  render(<Answer text={partial} streaming />);
  await screen.findByText("GPT");
});

test("a block that cannot render falls back to its source; the rest of the answer stays", async () => {
  const { container } = render(<Answer text={'前文\n```openui\nthis is not a program\n```\n后文'} />);
  await screen.findByText("无法显示此内容");
  assert.ok((container.textContent || "").includes("前文") && (container.textContent || "").includes("后文"));
});

test("a block named in Chinese renders and copies; one that resolves to empty layout falls back instead of blank space", async () => {
  const named = '```openui\nroot = Stack([表, 结论])\n数据 = [{名字: "甲", 值: 3}]\n表 = Table(["名称", "数量"], @Each(数据, "x", [x.名字, x.值]))\n结论 = Text(数据[0].名字 + " 有 " + 数据[0].值 + " 个")\n```';
  const { container } = render(<Answer text={named} />);
  await screen.findByText("甲 有 3 个");
  assert.equal(container.querySelectorAll("tbody tr").length, 1);
  assert.equal(answerForCopy(named), "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 3 |\n\n甲 有 3 个");
  cleanup();
  render(<Answer text={'前文\n```openui\nroot = Stack([missing, alsoMissing])\n```\n后文'} />);
  await screen.findByText("无法显示此内容");
});

test("markup the model writes is shown as text, never executed", async () => {
  const hostile = '```openui\nroot = Stack([Text("<img src=x onerror=alert(1)><script>alert(2)</script>"), Callout("javascript:alert(3)")])\n```';
  const { container } = render(<Answer text={hostile} />);
  await screen.findByText(/<script>/);
  assert.equal(container.querySelector("script"), null);
  assert.equal(container.querySelector("img"), null);
  assert.equal(container.querySelector("a[href^='javascript']"), null);
});

test("a button sends its @ToAssistant message through the host; read-only pages keep it disabled", async () => {
  const sent: string[] = [];
  const button = '```openui\nroot = Stack([Button("按天拆分", @ToAssistant("把使用量按天拆分"))])\n```';
  render(<GenUIHostProvider value={{ readOnly: false, send: async (t) => (sent.push(t), true) }}><Answer text={button} /></GenUIHostProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "按天拆分" }));
  await waitFor(() => assert.deepEqual(sent, ["把使用量按天拆分"]));
  cleanup();
  render(<Answer text={button} />);
  assert.equal((await screen.findByRole("button", { name: "按天拆分" })).hasAttribute("disabled"), true);
});

test("copy gives Markdown: metrics as list items, charts as tables", async () => {
  render(<Answer text={metrics} />);
  await screen.findByText("GPT"); // the chunk that converts is loaded with the first block
  const chart = '```openui\nroot = Stack([BarChart("使用量", ["GPT", "Claude"], [Series("次数", [320, 210])])])\n```';
  assert.equal(answerForCopy(`结论\n${metrics}`), "结论\n\n- GPT：320 次\n\n- Claude：210 次");
  assert.match(answerForCopy(chart), /\| GPT \| 320 \|/);
});

const plan = [
  "```openui",
  "root = Stack([inputs, total, ask])",
  '$tier = "标准版"',
  "$seats = 20",
  'inputs = Grid([Select("版本", ["标准版", "专业版"], $tier), Slider("人数", 5, 200, $seats, {step: 5, unit: "人"})])',
  'total = Metric("每月费用", $seats * ($tier == "专业版" ? 99 : 49), {unit: "元"})',
  'ask = Button("出报价单", @ToAssistant("按" + $tier + "、" + $seats + " 人出报价单"))',
  "```",
].join("\n");

test("controls write their $variable and every expression reading it updates in place; the button sends the evaluated text", async () => {
  const sent: string[] = [];
  const { container } = render(<GenUIHostProvider value={{ readOnly: false, send: async (t) => (sent.push(t), true) }}><Answer text={plan} /></GenUIHostProvider>);
  await screen.findByText("980");
  fireEvent.click(screen.getByRole("radio", { name: "专业版" }));
  await screen.findByText("1,980");
  fireEvent.change(container.querySelector("input[type=range]")!, { target: { value: "40" } });
  await screen.findByText("3,960");
  assert.equal(screen.getByRole("radio", { name: "专业版" }).getAttribute("aria-checked"), "true");
  fireEvent.click(screen.getByRole("button", { name: "出报价单" }));
  await waitFor(() => assert.deepEqual(sent, ["按专业版、40 人出报价单"]));
});

test("controls still work on a read-only page; only sending is off", async () => {
  const { container } = render(<Answer text={plan} />);
  await screen.findByText("980");
  fireEvent.change(container.querySelector("input[type=range]")!, { target: { value: "10" } });
  await screen.findByText("490");
  assert.equal(screen.getByRole("button", { name: "出报价单" }).hasAttribute("disabled"), true);
});

test("a table sorts by the header clicked: numbers by value, then descending, then back to the model's order", async () => {
  const table = '```openui\nroot = Stack([Table(["名称", "数量"], [["乙", 9], ["甲", 100], ["丙", 20]])])\n```';
  const { container } = render(<Answer text={table} />);
  await screen.findByText("甲");
  const order = () => [...container.querySelectorAll("tbody tr td:first-child")].map((td) => td.textContent);
  const head = screen.getByRole("button", { name: "数量" });
  fireEvent.click(head);
  assert.deepEqual(order(), ["乙", "丙", "甲"]);
  fireEvent.click(head);
  assert.deepEqual(order(), ["甲", "丙", "乙"]);
  fireEvent.click(head);
  assert.deepEqual(order(), ["乙", "甲", "丙"]);
});

test("a chart legend hides and shows a series", async () => {
  const chart = '```openui\nroot = Stack([LineChart("趋势", ["1月", "2月"], [Series("甲", [1, 2]), Series("乙", [3, 4])])])\n```';
  const { container } = render(<Answer text={chart} />);
  const toggle = await screen.findByRole("button", { name: "乙" });
  const lines = () => container.querySelectorAll("svg path[fill='none']").length;
  assert.equal(lines(), 2);
  fireEvent.click(toggle);
  assert.equal(toggle.getAttribute("aria-pressed"), "false");
  assert.equal(lines(), 1);
});

test("decimals fix the digits a number shows, on screen and in the copied Markdown; a delta is a rate in %", async () => {
  const block = '```openui\nroot = Stack([m, m2, t])\nm = Metric("转化率", 12.5, {unit: "%", change: 1.25, decimals: 2})\nm2 = Metric("留存", 40, {unit: "%", change: -4.25})\nt = Table(["地区", "占比"], [["华东", 0.3333], ["华北", 2]], {decimals: 1})\n```';
  const { container } = render(<Answer text={block} />);
  await screen.findByText("转化率");
  const text = container.textContent || "";
  assert.ok(text.includes("12.50") && text.includes("+1.3%") && text.includes("0.3") && text.includes("2.0"), text);
  assert.equal(answerForCopy(block), "- 转化率：12.50 %（+1.3%）\n\n- 留存：40 %（-4.3%）\n\n| 地区 | 占比 |\n| --- | --- |\n| 华东 | 0.3 |\n| 华北 | 2.0 |");
});

const stores = [
  "```openui",
  "root = Stack([pick, table, total, top])",
  '$region = "全部"',
  'items = [{name: "A店", region: "华东", amount: 420}, {name: "B店", region: "华西", amount: 358.8}, {name: "C店", region: "华东", amount: 442}]',
  'pick = Select("地区", ["全部", "华东", "华西"], $region)',
  'shown = $region == "全部" ? items : @Filter(items, "region", "==", $region)',
  'table = Table(["门店", "金额"], @Each(shown, "s", [s.name, s.amount]))',
  'total = Metric("合计", @Sum(shown.amount), {unit: "元"})',
  'best = @First(@Sort(shown, "amount", "desc"))',
  'top = Text(best.name + " 最高，为 " + best.amount + " 元")',
  "```",
].join("\n");

test("copy says what the page shows: rows, sums and conclusions from expressions, controls as the reader left them", async () => {
  render(<Answer text={stores} />);
  await screen.findByText("C店 最高，为 442 元");
  assert.equal(
    answerForCopy(stores),
    "- 地区：全部\n\n| 门店 | 金额 |\n| --- | --- |\n| A店 | 420 |\n| B店 | 358.8 |\n| C店 | 442 |\n\n- 合计：1,220.8 元\n\nC店 最高，为 442 元",
  );
  assert.equal(answerForCopy(`看：\n${stores}`, () => ({ $region: "华西" })), "看：\n\n- 地区：华西\n\n| 门店 | 金额 |\n| --- | --- |\n| B店 | 358.8 |\n\n- 合计：358.8 元\n\nB店 最高，为 358.8 元");
});

test("data that evaluates to the wrong shape shows as malformed, never as an empty table or a shifted chart", async () => {
  const block = [
    "```openui",
    "root = Stack([t, c, pie, ok])",
    'rows = [["A店", 420]]',
    't = Table(["门店", "金额"], rows + [["合计", 420]])',
    'c = BarChart("金额", ["A店"], [Series("金额", [420, 358])])',
    'pie = PieChart("预算", ["甲", "乙"], [Series("金额", [1, 2])])',
    'ok = Text("其余照常")',
    "```",
  ].join("\n");
  const { container } = render(<Answer text={block} />);
  await screen.findByText("其余照常");
  assert.equal(screen.getAllByText("数据格式有误，无法显示").length, 3);
  assert.equal(container.querySelector("table, svg"), null);
  assert.equal(answerForCopy(block), "其余照常");
  cleanup();
  // while the block is written a value can be missing for a moment: nothing is flagged yet
  render(<Answer text={block.replace(/\n```$/, "")} streaming />);
  await screen.findByText("其余照常");
  assert.equal(screen.queryByText("数据格式有误，无法显示"), null);
});

test("a generated page runs only in the sandboxed frame route; copy and print get its plain-text version", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ features: { genui_html: true } }), { status: 200 }));
  try {
    const page = "```openui\nroot = Stack([p])\np = HtmlArtifact(\"抛硬币\", '<button>抛</button><script>parent.x=1</script>', \"正面比例接近一半。\")\n```";
    const { container } = render(<Answer text={page} />);
    await waitFor(() => assert.ok(container.querySelector("iframe")));
    for (const frame of container.querySelectorAll("iframe")) {
      assert.equal(frame.getAttribute("sandbox"), "allow-scripts");
      assert.match(frame.getAttribute("src") || "", /^\/frame\/genui-(preview|run)$/);
      assert.equal(frame.hasAttribute("srcdoc"), false);
    }
    assert.equal(container.querySelector("script"), null); // nothing of the page lands in the Portal's document
    assert.equal(answerForCopy(page), "**抛硬币**\n\n正面比例接近一半。");
    assert.ok((container.querySelector(".gu-html-print")?.textContent || "").includes("正面比例接近一半。"));
  } finally {
    vi.unstubAllGlobals();
  }
});
