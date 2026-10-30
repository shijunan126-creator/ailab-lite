import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "vitest";
import { buildCatalog, buildDocs, buildGuide, buildIndex } from "../components/genui/catalog/prompt";
import { COMPONENT_NAMES } from "../components/genui/catalog/limits";
import { DEFINED_NAMES } from "../components/genui/catalog/schema";

const dir = new URL("../../ailab_platform/contracts/genui/", import.meta.url);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

test("the committed contract is what the catalog generates (run npm run genui:prompt)", () => {
  const index = buildIndex();
  const guide = buildGuide();
  assert.equal(readFileSync(new URL("openui-v2.index.md", dir), "utf8"), index);
  assert.equal(readFileSync(new URL("openui-v2.guide.md", dir), "utf8"), guide);
  assert.equal(readFileSync(new URL("catalog-v2.json", dir), "utf8"), JSON.stringify(buildCatalog(sha(index), sha(guide)), null, 2) + "\n");
});

test("the plain name list the chat loads matches the catalog definitions", () => {
  assert.deepEqual(DEFINED_NAMES, COMPONENT_NAMES);
});

test("the index names every component, and each doc starts with its signature", () => {
  const index = buildIndex();
  for (const name of COMPONENT_NAMES) assert.match(index, new RegExp(`[：、 ]${name}(?![A-Za-z])`), name);
  for (const { name, doc, needs } of buildDocs()) {
    assert.match(doc, new RegExp(`^${name}\\(`), name);
    for (const sub of needs) assert.ok(COMPONENT_NAMES.includes(sub), `${name} needs ${sub}`);
  }
});

test("the contract offers only what the Portal renders and allows; HtmlArtifact can be cut out by line", () => {
  const all = [buildIndex(), buildGuide(), ...buildDocs().map((d) => d.doc)].join("\n");
  for (const banned of ["@OpenUrl", "Query(", "Mutation(", "@Set", "forms"]) assert.equal(all.includes(banned), false, banned);
  // the Agent drops lines naming HtmlArtifact when pages are off: what is left must not depend on them
  assert.equal(buildGuide().includes("HtmlArtifact"), false);
  assert.equal(buildIndex().split("\n").filter((l) => l.includes("HtmlArtifact")).length, 1);
});
