import { brotliDecompressSync, gunzipSync } from "node:zlib";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { assetEncoding } from "../office/encoding";

// Exercise the real route against the generated closure, including its compressed bytes and version fence.
const route = "../app/office-viewer/[version]/[...path]/route";
afterEach(() => vi.restoreAllMocks());
test.each([
  [null, null], ["gzip, deflate, br", "br"], ["gzip;q=1, br;q=0.5", "gzip"],
  ["br;q=0, gzip;q=0", null], ["*;q=0.8, br;q=0", "gzip"], ["br;q=bad", null],
])("asset encoding negotiation %s", (header, expected) => expect(assetEncoding(header)).toBe(expected));

test("versioned viewer serves negotiated bytes, immutable cache and Vary; stale versions fail", async () => {
  const { GET } = await import(route);
  const root = path.join(process.cwd(), "office/dist");
  const { version } = JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8"));
  const original = await readFile(path.join(root, "viewer-docx.js"));
  for (const encoding of ["br", "gzip", "identity"]) {
    const response = await GET(new Request("http://localhost", { headers: { "Accept-Encoding": encoding } }), { params: Promise.resolve({ version, path: ["viewer-docx.js"] }) });
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toContain("immutable");
    expect(response.headers.get("Vary")).toBe("Accept-Encoding");
    const bytes = Buffer.from(await response.arrayBuffer());
    expect(Number(response.headers.get("Content-Length"))).toBe(bytes.length);
    const decoded = encoding === "br" ? brotliDecompressSync(bytes) : encoding === "gzip" ? gunzipSync(bytes) : bytes;
    expect(decoded.equals(original)).toBe(true);
    expect(response.headers.get("Content-Encoding")).toBe(encoding === "identity" ? null : encoding);
  }
  expect((await GET(new Request("http://localhost"), { params: Promise.resolve({ version: "old", path: ["viewer-docx.js"] }) })).status).toBe(404);
});
