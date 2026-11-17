// @vitest-environment jsdom
// TeX in answers and messages: which text is math, how display blocks are read, and what the reader sees.
import assert from "node:assert/strict";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, test } from "vitest";
import Markdown from "../components/markdown";
import MessageText from "../components/chat/message-text";
import { cellText } from "../lib/clipboard";
import { holdOpenMath, readMathBlock, splitMath, tableCells } from "../lib/math";

afterEach(cleanup);

const maths = (text: string) => splitMath(text).filter((p) => typeof p !== "string").map((p) => (typeof p === "string" ? p : p.tex));

test("dollar signs: formulas are math, prices and escaped dollars stay text", () => {
  assert.deepEqual(maths("设 $I = A\\pi a^B$, 其中 $\\Sigma$ 为球面"), ["I = A\\pi a^B", "\\Sigma"]);
  assert.deepEqual(maths("售价 $5 起，会员 $3"), []);
  assert.deepEqual(maths("$5-$10 之间"), []);
  assert.deepEqual(maths("from $100 to $200 dollars"), []);
  assert.deepEqual(maths("$ x $ 不是公式"), []);
  assert.deepEqual(splitMath("价格 \\$5，公式 $x$"), ["价格 $5，公式 ", { tex: "x", display: false, raw: "$x$" }]);
  assert.deepEqual(maths("\\(a_1\\) 与 \\[b\\] 与 $$c$$"), ["a_1", "b", "c"]);
  assert.deepEqual(maths("$a\\$b$"), ["a\\$b"]);
});

test("display blocks: \\[ \\], $$ and bare environments, closed or still arriving", () => {
  const lines = ["\\[", "I = \\iint_D f", "\\]", "后文"];
  assert.deepEqual(readMathBlock(lines, 0), { tex: "I = \\iint_D f", display: true, next: 3, closed: true, rest: undefined });
  assert.equal(readMathBlock(["$$x^2$$"], 0)?.tex, "x^2");
  // a line that starts with inline display math is a paragraph, not a block
  assert.equal(readMathBlock(["$$x$$ 是平方"], 0), null);
  const env = readMathBlock(["\\begin{aligned}", "a &= b \\\\", "c &= d", "\\end{aligned}"], 0);
  assert.equal(env?.tex, "\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}");
  const tail = readMathBlock(["$$", "x", "$$ 所以成立"], 0);
  assert.equal(tail?.rest, "所以成立");
  assert.deepEqual(readMathBlock(["\\[", "x = "], 0), { tex: "x =", display: true, next: 2, closed: false });
  // a stray opener does not swallow the answer after it
  assert.equal(readMathBlock(["$$", "", "下一段"], 0), null);
});

test("table cells keep pipes inside math and code, and read \\| as a pipe", () => {
  assert.deepEqual(tableCells("| $|x|$ | `a|b` | a \\| b | $\\|v\\|$ |"), ["$|x|$", "`a|b`", "a | b", "$\\|v\\|$"]);
});

test("a streaming \\( waits for its \\) before showing", () => {
  assert.equal(holdOpenMath("因为 \\(x^2"), "因为");
  assert.equal(holdOpenMath("因为 \\(x\\) 成立"), "因为 \\(x\\) 成立");
  assert.equal(holdOpenMath("```\nre = r'\\('"), "```\nre = r'\\('");
});

const ANSWER = [
  "在球面上 \\(x^2+y^2+z^2=a^2\\)，所以分母为 \\(a\\)。令",
  "",
  "\\[",
  "s=\\sqrt{a^2-x^2-y^2},\\qquad z=-s.",
  "\\]",
  "",
  "1. 第一项：",
  "   \\[",
  "   \\iint_D \\frac{x^2}{s}\\,dxdy=\\frac{2\\pi a^3}{3}",
  "   \\]",
  "2. 所以 $A*B$ 与 *强调*：",
  "",
  "| 量 | 值 |",
  "|---|---|",
  "| $|I|$ | $\\frac{\\pi a^3}{2}$ |",
  "",
  "\\[",
  "\\boxed{A+B=2}",
  "\\]",
].join("\n");

test("an answer's formulas are typeset, with no TeX source left in the text", async () => {
  const { container } = render(<Markdown text={ANSWER} />);
  await waitFor(() => assert.equal(container.querySelectorAll(".md-math.source").length, 0));
  const display = container.querySelectorAll(".md-math.display .katex-display");
  assert.equal(display.length, 3);
  // the formula under a list item stays in that item, and the list keeps its numbering
  assert.ok(container.querySelector("ol li .md-math.display .katex-display"));
  assert.equal(container.querySelectorAll("ol").length, 1);
  assert.equal(container.querySelectorAll("ol > li").length, 2);
  // * inside a formula is TeX, not emphasis; outside it still is
  assert.equal(container.querySelectorAll("em").length, 1);
  // |x| inside a cell does not split the row
  assert.equal(container.querySelectorAll("tbody td").length, 2);
  assert.ok(container.querySelector("tbody td .katex"));
  const visible = Array.from(container.querySelectorAll(".katex-html")).map((n) => n.textContent).join("");
  assert.ok(!/\\(frac|sqrt|boxed|iint)/.test(visible));
  // copying a table cell takes the formula's TeX
  assert.equal(cellText(container.querySelector("tbody td")!), "$|I|$");
});

test("TeX that does not parse shows its source; trusted commands are never typeset", async () => {
  const { container } = render(<Markdown text={"坏公式 $\\frac{1}{$ 与 $\\href{javascript:alert(1)}{x}$ 与 $\\badcommand$"} />);
  await waitFor(() => assert.equal(container.querySelectorAll(".md-math.source").length, 0));
  assert.equal(container.querySelector("a[href^='javascript']"), null);
  const errors = Array.from(container.querySelectorAll(".md-math.error"));
  assert.ok(errors.some((e) => e.textContent === "$\\badcommand$"));
  assert.ok(errors.every((e) => e.getAttribute("title")));
});

test("while streaming, an open display block is a placeholder, not raw TeX", async () => {
  const { container, rerender } = render(<Markdown text={"推导：\n\n\\[\nI = \\int_0^a"} streaming />);
  assert.ok(container.querySelector(".md-math.pending"));
  assert.ok(!container.textContent!.includes("\\int"));
  rerender(<Markdown text={"推导：\n\n\\[\nI = \\int_0^a x\\,dx\n\\]"} streaming />);
  await waitFor(() => assert.ok(container.querySelector(".md-math.display .katex-display")));
  assert.equal(container.querySelector(".md-math.pending"), null);
  // finished without its closing delimiter: the text is shown as written
  rerender(<Markdown text={"推导：\n\n$$\nI = 1"} />);
  assert.equal(container.querySelector(".md-math.pending"), null);
  assert.ok(container.textContent!.includes("I = 1"));
});

test("a user's message typesets its formulas and keeps prices as typed", async () => {
  const { container } = render(<MessageText math parts={[{ kind: "text", text: "设 $\\Sigma$ 为球面，预算 $5 到 $8" }]} />);
  await waitFor(() => assert.ok(container.querySelector(".katex")));
  assert.equal(container.querySelectorAll(".katex").length, 1);
  assert.ok(container.textContent!.includes("预算 $5 到 $8"));
});

test("chemistry loads mhchem on demand", async () => {
  const { container } = render(<Markdown text={"反应 $\\ce{2H2 + O2 -> 2H2O}$"} />);
  await waitFor(() => assert.ok(container.querySelector(".katex")));
  assert.equal(container.querySelector(".md-math.error"), null);
});

test("an inline formula piece wider than its line scrolls by itself; the rest of the line wraps as usual", async () => {
  // jsdom has no layout: a line 343px wide (a phone), and a fraction piece wider than it
  const width = (el: Element) => (el.matches(".katex-html > .katex-base") ? (el.querySelector(".mfrac") ? 500 : 40) : el.matches("p") ? 343 : 0);
  const scroll = Object.getOwnPropertyDescriptor(Element.prototype, "scrollWidth")!;
  const client = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth")!;
  Object.defineProperty(Element.prototype, "scrollWidth", { configurable: true, get() { return width(this); } });
  Object.defineProperty(Element.prototype, "clientWidth", { configurable: true, get() { return width(this); } });
  try {
    const { container } = render(<Markdown text={"设 $\\left(\\frac{a_1+a_2}{b}\\right) = x + y$ 成立"} />);
    await waitFor(() => assert.ok(container.querySelector(".katex")));
    const pieces = Array.from(container.querySelectorAll(".katex-html > .katex-base"));
    assert.ok(pieces.length > 1);
    assert.deepEqual(pieces.map((p) => p.classList.contains("wide")), pieces.map((p) => !!p.querySelector(".mfrac")));
  } finally {
    Object.defineProperty(Element.prototype, "scrollWidth", scroll);
    Object.defineProperty(Element.prototype, "clientWidth", client);
  }
});
