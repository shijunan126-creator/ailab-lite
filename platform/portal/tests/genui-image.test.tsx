// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createParser } from "@openuidev/react-lang";
import { afterEach, test, vi } from "vitest";
import Answer from "../components/chat/answer";
import { library } from "../components/genui/catalog/components";
import { toProgram } from "../components/genui/catalog/program";
import { fitScale, imageName, titleOf } from "../components/genui/image";
import { pictured } from "../components/genui/openui/block";
import { tableClip, writeClipboard } from "../lib/clipboard";

vi.mock("../components/genui/telemetry", () => ({ report: () => undefined }));
afterEach(cleanup);

const parser = createParser(library.toJSONSchema(), "Stack");
const picture = (source: string) => pictured(parser.parse(toProgram(source)));
const fence = (program: string) => "```openui\n" + program + "\n```";

test("a block is offered as a picture unless it is controls, text, a table, code or a generated page", () => {
  assert.equal(picture('root = Stack([Button("按月拆分", @ToAssistant("按月拆分"))])'), false);
  assert.equal(picture('root = Stack([Heading("明细"), Table(["a", "b"], [["x", 1]])])'), false);
  assert.equal(picture('root = Compare(["A", "B"], [{label: "价格", values: [1, 2]}])'), false);
  assert.equal(picture('root = Code("print(1)", {language: "python"})'), false);
  assert.equal(picture('root = HtmlArtifact("页面", "<p>x</p>", "x")'), false);
  assert.equal(picture('root = Stack([Metric("请求", 3), Table(["a"], [["x"]])])'), true);
  assert.equal(picture('root = Stack([Text("说明"), Button("继续", @ToAssistant("继续")), Metric("请求", 3)])'), true);
  // components an expression builds are known by name before they are evaluated
  assert.equal(picture('items = [{n: "a", v: 1}]\nroot = Stack(@Each(items, "x", Metric(x.n, x.v)))'), true);
  assert.equal(picture('items = [{n: "a"}]\nroot = Stack(@Each(items, "x", Text(x.n)))'), false);
});

test("the picture stays sharp and within what a canvas can hold", () => {
  assert.equal(fitScale(800, 600, 1), 2);
  assert.equal(fitScale(800, 600, 2.5), 2.5);
  assert.equal(fitScale(800, 600, 4), 3);
  const tall = fitScale(800, 12000, 2);
  assert.ok(tall < 2 && 800 * 12000 * tall * tall <= 16_000_000 && 12000 * tall <= 16_000);
});

test("the picture is named after the block's first title, without characters file systems refuse", () => {
  assert.equal(imageName('收入/成本: "Q3"'), "收入 成本 Q3.png");
  assert.equal(imageName("  ", new Date(2026, 10, 1, 9, 5, 7)), "图表-20261101-090507.png");
  const el = document.createElement("div");
  el.innerHTML = '<figure class="gu-chart"><figcaption>月营收<span class="gu-chart-unit">万元</span></figcaption></figure>';
  assert.equal(titleOf(el), "月营收");
});

function table(html: string): HTMLTableElement {
  const el = document.createElement("div");
  el.innerHTML = html;
  return el.querySelector("table")!;
}

test("a table copies as an HTML table and as Markdown with numbers aligned right", () => {
  const clip = tableClip(table(
    '<table><thead><tr><th><button>地区<span aria-hidden="true"><svg></svg></span></button></th><th class="num"><button>营收</button></th></tr></thead>' +
      '<tbody><tr><td>华东 | 上海</td><td class="num">1,280</td></tr><tr><td>华<b>北</b></td><td class="num">96</td></tr></tbody></table>',
  ));
  assert.equal(clip.plain, "| 地区 | 营收 |\n| --- | ---: |\n| 华东 \\| 上海 | 1,280 |\n| 华北 | 96 |");
  assert.match(clip.html!, /^<table[^>]*><tr><th[^>]*>地区<\/th><th[^>]*text-align:right[^>]*>营收<\/th><\/tr>/);
  assert.ok(clip.html!.includes(">华东 | 上海</td>") && clip.html!.includes(">1,280</td>"));
});

test("a comparison copies its marks by their labels and leaves out text meant for screen readers", () => {
  const clip = tableClip(table(
    '<table><thead><tr><th></th><th>方案 A<span class="gu-badge">推荐</span></th></tr></thead>' +
      '<tbody><tr><th scope="row">售后</th><td class="gu-best"><span class="gu-yes" aria-label="是"><svg></svg></span><span class="sr-only">（最优）</span></td></tr></tbody></table>',
  ));
  assert.equal(clip.plain, "|  | 方案 A 推荐 |\n| --- | --- |\n| 售后 | 是 |");
});

test("a Markdown table copies the Markdown it was written in", async () => {
  const md = "| 名称 | 数量 |\n|---|--:|\n| **苹果** | 3 |";
  const writes: ClipboardItem[][] = [];
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  vi.stubGlobal("ClipboardItem", class { constructor(public items: Record<string, Blob>) {} });
  Object.defineProperty(navigator, "clipboard", { value: { write: async (items: ClipboardItem[]) => void writes.push(items) }, configurable: true });
  const { container } = render(<Answer text={`看表：\n\n${md}\n\n完。`} />);
  const button = screen.getByRole("button", { name: "复制表格" });
  assert.ok(container.querySelector(".table-frame > .md-table table"));
  fireEvent.click(button);
  await waitFor(() => assert.equal(writes.length, 1));
  const items = (writes[0][0] as unknown as { items: Record<string, Blob> }).items;
  assert.equal(await items["text/plain"].text(), md);
  assert.match(await items["text/html"].text(), /<td[^>]*>苹果<\/td>/);
  await waitFor(() => assert.ok(screen.getByRole("button", { name: "已复制" })));
  vi.unstubAllGlobals();
});

test("without a secure context a copy still sets both flavours through a copy event", async () => {
  Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
  const set: Record<string, string> = {};
  document.execCommand = ((command: string) => {
    const event = new Event("copy", { cancelable: true }) as ClipboardEvent;
    Object.defineProperty(event, "clipboardData", { value: { setData: (type: string, value: string) => void (set[type] = value) } });
    document.dispatchEvent(event);
    return command === "copy";
  }) as typeof document.execCommand;
  await writeClipboard({ plain: "| a |", html: "<table></table>" });
  assert.deepEqual(set, { "text/plain": "| a |", "text/html": "<table></table>" });
});

test("a finished block offers picture actions; a table-only block offers only the table's copy", async () => {
  const chart = fence('root = BarChart("请求", ["成功", "失败"], [Series("次数", [300, 20])])');
  const tableOnly = fence('root = Stack([Heading("明细"), Table(["地区", "营收"], [["华东", 520]])])');
  const buttons = fence('root = Stack([Button("按天拆分", @ToAssistant("按天拆分"))])');
  const { container, rerender } = render(<Answer text={`${chart}\n${tableOnly}\n${buttons}`} />);
  await screen.findByText("按天拆分");
  await waitFor(() => assert.equal(container.querySelectorAll(".genui-block[data-state='ok']").length, 3));
  const blocks = container.querySelectorAll(".genui-block");
  assert.ok(blocks[0].querySelector(":scope > .genui-tools[data-capture-skip]"));
  assert.ok(screen.getByRole("button", { name: "下载图片" }));
  assert.equal(blocks[1].querySelector(".genui-tools"), null);
  assert.ok(blocks[1].querySelector(".table-frame .table-tools [aria-label='复制表格']"));
  assert.equal(blocks[2].querySelector(".genui-tools"), null);
  rerender(<Answer text={fence('root = BarChart("请求", ["成功"], [Series("次数", [3])])').replace(/\n```$/, "")} streaming />);
  await waitFor(() => assert.equal(container.querySelectorAll(".genui-tools").length, 0));
});

test("a touch shows a block's actions until a touch lands outside it", async () => {
  document.elementsFromPoint = () => []; // jsdom has no layout
  const { container } = render(<Answer text={fence('root = Metric("请求", 3)')} />);
  await waitFor(() => assert.ok(container.querySelector(".genui-tools")));
  const block = container.querySelector(".genui-block")!;
  fireEvent.pointerDown(block, { pointerType: "touch" });
  await waitFor(() => assert.ok(block.hasAttribute("data-touched")));
  fireEvent.pointerDown(document.body, { pointerType: "touch" });
  await waitFor(() => assert.ok(!block.hasAttribute("data-touched")));
  fireEvent.pointerDown(block, { pointerType: "mouse" });
  assert.ok(!block.hasAttribute("data-touched"));
});
