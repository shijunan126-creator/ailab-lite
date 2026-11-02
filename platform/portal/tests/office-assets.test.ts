import { brotliCompressSync, brotliDecompressSync, gzipSync, gunzipSync } from "node:zlib";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { assetEncoding } from "../office/encoding";

// Exercise the real route against a build-shaped fixture (npm test runs before the office build in CI),
// including its precompressed bytes and version fence.
const route = "../app/office-viewer/[version]/[...path]/route";
let cwd: string | undefined;
afterEach(async () => {
  vi.restoreAllMocks();
  vi.resetModules();
  if (cwd) await rm(cwd, { recursive: true, force: true });
  cwd = undefined;
});
test.each([
  [null, null], ["gzip, deflate, br", "br"], ["gzip;q=1, br;q=0.5", "gzip"],
  ["br;q=0, gzip;q=0", null], ["*;q=0.8, br;q=0", "gzip"], ["br;q=bad", null],
])("asset encoding negotiation %s", (header, expected) => expect(assetEncoding(header)).toBe(expected));

test("versioned viewer serves negotiated bytes, immutable cache and Vary; stale versions fail", async () => {
  cwd = await mkdtemp(path.join(tmpdir(), "office-viewer-"));
  const root = path.join(cwd, "office", "dist");
  await mkdir(root, { recursive: true });
  const version = "0123456789abcdef";
  const original = Buffer.from("console.log('viewer');\n".repeat(64));
  await writeFile(path.join(root, "manifest.json"), JSON.stringify({ version }));
  await writeFile(path.join(root, "viewer-docx.js"), original);
  await writeFile(path.join(root, "viewer-docx.js.br"), brotliCompressSync(original));
  await writeFile(path.join(root, "viewer-docx.js.gz"), gzipSync(original));
  vi.spyOn(process, "cwd").mockReturnValue(cwd);
  const { GET } = await import(route);
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
