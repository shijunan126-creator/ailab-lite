import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "vitest";
import { answerMarkdown, insideUiFence, splitAnswerParts } from "../lib/answer-parts";

const block = "```openui\nroot = Stack([Text(\"hi\")])\n```";

test("text and UI blocks interleave in order, each block with its own index", () => {
  const parts = splitAnswerParts(`最高的是 GPT：\n\n${block}\n\n占一半。\n\n${block}`);
  assert.deepEqual(parts.map((p) => [p.kind, p.index, p.closed]), [["md", 0, true], ["ui", 1, true], ["md", 2, true], ["ui", 3, true]]);
  assert.equal(parts[1].text, 'root = Stack([Text("hi")])\n');
  assert.equal(parts[0].text, "最高的是 GPT：\n\n");
});

test("openui inside another code fence is code, and a longer fence closes only with as many backticks", () => {
  const nested = "```md\n```openui\nroot = Stack([])\n```\n";
  assert.deepEqual(splitAnswerParts(nested).map((p) => p.kind), ["md"]);
  const four = "````openui\nroot = Stack([])\n```\nstill inside\n````\nafter";
  const parts = splitAnswerParts(four);
  assert.deepEqual(parts.map((p) => p.kind), ["ui", "md"]);
  assert.match(parts[0].text, /still inside/);
});

test("an unclosed last block streams as an open part; a half-written fence line waits for its newline", () => {
  const parts = splitAnswerParts("看下面：\n```openui\nroot = Stack([m", true);
  assert.deepEqual(parts.map((p) => [p.kind, p.closed]), [["md", true], ["ui", false]]);
  assert.deepEqual(splitAnswerParts("看下面：\n```op", true).map((p) => p.text), ["看下面：\n"]);
  assert.deepEqual(splitAnswerParts("看下面：\n``", true).map((p) => p.text), ["看下面：\n"]);
  assert.deepEqual(splitAnswerParts("看下面：\n```python", true).map((p) => p.text), ["看下面：\n```python"]);
  assert.equal(insideUiFence("a\n```openui\nroot = "), true);
  assert.equal(insideUiFence(`a\n${block}\n`), false);
});

test("CRLF answers split the same way, and copy turns blocks into their Markdown", () => {
  assert.deepEqual(splitAnswerParts(`a\r\n${block.replace(/\n/g, "\r\n")}\r\nb`).map((p) => p.kind), ["md", "ui", "md"]);
  assert.equal(answerMarkdown(`a\n${block}\nb`, () => "- hi"), "a\n\n- hi\n\nb");
});

test("the shared fixtures split exactly as in Python", () => {
  const dir = new URL("../../tests/fixtures/genui/", import.meta.url);
  const names = readdirSync(dir).filter((f) => f.endsWith(".md"));
  assert.ok(names.length >= 7);
  for (const name of names) {
    const text = readFileSync(new URL(name, dir), "utf8");
    const expected = JSON.parse(readFileSync(new URL(name.replace(/\.md$/, ".json"), dir), "utf8"));
    assert.deepEqual(splitAnswerParts(text), expected, name);
  }
});
