import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, test, vi } from "vitest";

// Exercise the route before Next's HTTP server applies compression.
const route = "../app/office-viewer/[version]/[...path]/route";
let cwd: string | undefined;
afterEach(async () => {
  vi.restoreAllMocks();
  vi.resetModules();
  if (cwd) await rm(cwd, { recursive: true, force: true });
  cwd = undefined;
});
test("versioned viewer leaves compression to the HTTP server and rejects stale versions", async () => {
  cwd = await mkdtemp(path.join(tmpdir(), "office-viewer-"));
  const root = path.join(cwd, "office", "dist");
  await mkdir(root, { recursive: true });
  const version = "0123456789abcdef";
  const original = Buffer.from("console.log('viewer');\n".repeat(64));
  await writeFile(path.join(root, "manifest.json"), JSON.stringify({ version }));
  await writeFile(path.join(root, "viewer-docx.js"), original);
  vi.spyOn(process, "cwd").mockReturnValue(cwd);
  const { GET } = await import(route);
  const request = new Request("http://localhost", { headers: { "Accept-Encoding": "br, gzip" } });
  const response = await GET(request, { params: Promise.resolve({ version, path: ["viewer-docx.js"] }) });
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toContain("immutable");
  expect(response.headers.has("Content-Encoding")).toBe(false);
  expect(Buffer.from(await response.arrayBuffer())).toEqual(original);
  expect((await GET(new Request("http://localhost"), { params: Promise.resolve({ version: "old", path: ["viewer-docx.js"] }) })).status).toBe(404);
});
