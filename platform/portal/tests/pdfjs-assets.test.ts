import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";
import { GET } from "../app/pdfjs/[version]/[kind]/[name]/route";

const installed = JSON.parse(readFileSync(new URL("../node_modules/pdfjs-dist/package.json", import.meta.url), "utf8")).version;
const get = (version: string, kind: string, name: string) => GET(new Request("http://portal/"), { params: Promise.resolve({ version, kind, name }) });

test("serves the PDF.js CMaps, fonts and decoders of the installed version, immutably", async () => {
  const cmap = await get(installed, "cmaps", "UniGB-UCS2-H.bcmap");
  assert.equal(cmap.status, 200);
  assert.ok((await cmap.arrayBuffer()).byteLength > 0);
  assert.match(cmap.headers.get("Cache-Control") ?? "", /immutable/);
  const wasm = await get(installed, "wasm", "openjpeg.wasm");
  assert.equal(wasm.headers.get("Content-Type"), "application/wasm");
  assert.equal((await get(installed, "standard_fonts", "LiberationSans-Regular.ttf")).status, 200);
});

test("refuses other versions, directories and paths", async () => {
  assert.equal((await get("0.0.0", "cmaps", "UniGB-UCS2-H.bcmap")).status, 404);
  assert.equal((await get(installed, "build", "pdf.mjs")).status, 404);
  assert.equal((await get(installed, "cmaps", "../package.json")).status, 404);
  assert.equal((await get(installed, "cmaps", ".hidden")).status, 404);
  assert.equal((await get(installed, "cmaps", "missing.bcmap")).status, 404);
});
