import assert from "node:assert/strict";
import { test } from "vitest";
import { createParser } from "@openuidev/lang-core";
import { toProgram } from "../components/genui/catalog/program";
import { schemaLibrary } from "../components/genui/catalog/schema";

test("names the lexer would cut are renamed consistently; strings and comments stay as written", () => {
  const source = [
    "root = Stack([表, n_0])",
    "# it's a comment with 中文",
    "$座位 = 3",
    'm = {名字: "甲", v: 1.5}',
    '表 = Text("表 " + $座位 + \'个\' + m.名字)',
    'n_0 = Text("已有的名字")',
  ].join("\n");
  assert.equal(
    toProgram(source),
    [
      "root = Stack([n_1, n_0])",
      "# it's a comment with 中文",
      "$n_2 = 3",
      'm = {n_3: "甲", v: 1.5}',
      'n_1 = Text("表 " + $n_2 + \'个\' + m.n_3)',
      'n_0 = Text("已有的名字")',
    ].join("\n"),
  );
  const plain = 'root = Stack([t])\nt = Text("只有字符串里有中文")';
  assert.equal(toProgram(plain), plain);
});

test("a single-quoted string is raw text: only \\' is an escape, so a page's JavaScript arrives as written", () => {
  const page = String.raw`<script>html+="<div class=\"row\">"+n+"</div>\n";/\d+/.test(s);t=\'it\\'s\'</script>`;
  const source = `root = Stack([page])\npage = HtmlArtifact("页", '${page}', "要点")`;
  const parsed = (text: string) => JSON.stringify(createParser(schemaLibrary.toJSONSchema(), "Stack").parse(toProgram(text)).root);
  assert.ok(parsed(source).includes(JSON.stringify(String.raw`<script>html+="<div class=\"row\">"+n+"</div>\n";/\d+/.test(s);t='it\'s'</script>`)));
  // double-quoted strings keep JSON escapes
  assert.ok(parsed('root = Stack([t])\nt = Text("a \\"b\\"\\n")').includes(JSON.stringify('a "b"\n')));
});
