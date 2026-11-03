// @vitest-environment jsdom
// The library viewer and Work tabs open Word/Excel/PowerPoint/PDF natively first; the server's paged preview is asked
// for only when the native viewer cannot show the file.
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FilePreviewBody, FileSourceContext, type FileSource } from "../components/preview/file-viewer";
import PreviewPane from "../components/chat/preview-pane";
import type { FileItem, Preview } from "../lib/api";
import { frameBridge } from "./office-bridge";

vi.mock("../components/preview/pdf", () => ({ default: ({ title }: { title: string }) => <div>pdf:{title}</div> }));

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const ready = { status: "ready", native: true, version: "hash1", bytes: 3, units: 4, outline: [] };
let routes: Record<string, () => Response>;
let fetched: string[];
const file = (over: Partial<FileItem> = {}): FileItem => ({
  id: "f1", filename: "报告.pdf", content_type: "application/pdf", size: 3, status: "ready", version: 1, meta: {}, created_at: "", download_url: "/v1/files/f1/content", ...over,
});

beforeEach(() => {
  fetched = [];
  routes = {};
  vi.stubGlobal("React", React);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input).replace(/^https?:\/\/[^/]+/, "");
    fetched.push(`${init?.method || "GET"} ${url}`);
    if (url.endsWith("manifest.json")) return json({ version: "v" });
    if (url.startsWith("/office-viewer/")) return new Response("bundle");
    const route = Object.keys(routes).find((key) => url.includes(key));
    if (route) return routes[route]();
    if (url.endsWith("/content")) return new Response(new Uint8Array([1, 2, 3]));
    return json({ detail: "not found" }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const asked = (part: string) => fetched.filter((line) => line.includes(part));

test("a library PDF opens natively, offers no selection actions and never asks for the server preview", async () => {
  routes["/document"] = () => json(ready);
  render(<FilePreviewBody file={file()} />);
  const b = await frameBridge("报告.pdf");
  await b.send({ t: "ready", viewId: "v1" });
  expect(b.post.mock.calls.find(([m]) => m.t === "context")![0].context).toMatchObject({ chat: false });
  await b.send({ t: "call", id: 1, name: "report_view", args: { at: "page:2", selection: { at: "page:2", text: "x" } } });
  await b.send({ t: "reference", id: 2, reference: { path: "报告.pdf", at: "page:2", text: "x" } });
  expect(b.reply(2)).toMatchObject({ ok: false });
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(asked("/conversations/")).toEqual([]);
  expect(asked("/preview")).toEqual([]);
  expect(asked("/v1/files/f1/document")).toHaveLength(1);
});

test("a cited PDF page and slide open there; a cited Word page uses the paged preview where that page exists", async () => {
  routes["/document"] = () => json(ready);
  render(<FilePreviewBody file={file()} page={3} />);
  const b = await frameBridge("报告.pdf");
  await b.send({ t: "ready", viewId: "v1" });
  expect(b.post.mock.calls.find(([m]) => m.t === "snapshot")![0].snapshot.reveal.targets).toEqual([{ at: "page:3" }]);
  cleanup();
  fetched = [];
  routes["/preview"] = () => json({ status: "pending" });
  render(<FilePreviewBody file={file({ filename: "报告.docx", content_type: "" })} page={3} />);
  await waitFor(() => expect(asked("/v1/files/f1/preview")).toHaveLength(1));
  expect(asked("/document")).toEqual([]);
});

test("not native, too large or trashed: the paged preview, asked for only then", async () => {
  routes["/document"] = () => json({ ...ready, native: false });
  routes["/preview"] = () => json({ status: "pending" });
  render(<FilePreviewBody file={file()} />);
  await waitFor(() => expect(asked("/v1/files/f1/preview")).toHaveLength(1));
  const at = (part: string) => fetched.findIndex((line) => line.includes(part));
  expect(at("/v1/files/f1/document")).toBeGreaterThanOrEqual(0);
  expect(at("/v1/files/f1/document")).toBeLessThan(at("/v1/files/f1/preview"));
  cleanup();
  for (const over of [{ size: 64 * 1024 * 1024 + 1 }, { deleted_at: "2026-11-01T00:00:00Z" }]) {
    fetched = [];
    render(<FilePreviewBody file={file(over)} />);
    await waitFor(() => expect(asked("/v1/files/f1/preview")).toHaveLength(1));
    expect(asked("/document")).toEqual([]);
    cleanup();
  }
});

test("a shared file reads its index and bytes through the share", async () => {
  routes["/document"] = () => json(ready);
  const share: FileSource = { preview: (id) => `/v1/shares/s1/files/${id}/preview`, asset: () => "", shareId: "s1" };
  render(<FileSourceContext.Provider value={share}><FilePreviewBody file={file()} /></FileSourceContext.Provider>);
  const b = await frameBridge("报告.pdf");
  await b.send({ t: "ready", viewId: "v1" });
  await b.send({ t: "call", id: 1, name: "load_document", args: { path: "报告.pdf" } });
  await waitFor(() => expect(asked("/v1/shares/s1/files/f1/content")).toHaveLength(1));
  expect(asked("/v1/shares/s1/files/f1/document")).toHaveLength(1);
  expect(asked("/v1/files/")).toEqual([]);
});

test("closing the viewer stops a download the frame has not taken", async () => {
  routes["/document"] = () => json(ready);
  const view = render(<FilePreviewBody file={file()} />);
  const b = await frameBridge("报告.pdf");
  await b.send({ t: "ready", viewId: "v1" });
  await b.send({ t: "call", id: 1, name: "load_document", args: { path: "报告.pdf" } });
  const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/content"))!;
  view.unmount();
  expect((call[1] as RequestInit).signal!.aborted).toBe(true);
});

const pane = (preview: Preview, delivered: Record<string, string> = {}) => ({
  previews: [preview], selected: preview.filename, onSelect: vi.fn(), onCloseFile: vi.fn(), sources: null, sourcesOn: false,
  onSourcesTab: vi.fn(), onCloseSources: vi.fn(), readers: [], readerUrl: null, readerOn: false, onReaderTab: vi.fn(),
  onCloseReader: vi.fn(), onRead: vi.fn(), onClose: vi.fn(), live: true, downloads: {}, conversationId: "c1", delivered,
  onReference: vi.fn(), onAsk: vi.fn().mockResolvedValue(undefined),
});
const native = { filename: "草稿.docx", path: "/workspace/outputs/草稿.docx", run_id: "r1", version: 2, kind: "native" as const };

test("a Work file opens natively; the workspace render is asked for only when the native viewer cannot show it", async () => {
  routes["/workspace/document"] = () => json(ready);
  const view = render(<PreviewPane {...pane(native)} />);
  await frameBridge("草稿.docx");
  expect(asked("/v1/runs/r1/workspace/document?path=outputs%2F")).toHaveLength(1);
  expect(asked("/workspace/preview")).toEqual([]);
  view.unmount();
  fetched = [];
  routes["/workspace/document"] = () => json({ ...ready, native: false });
  routes["/workspace/preview"] = () => json({ filename: "草稿.docx", version: 2, kind: "pdf", pdf: "2/document.pdf" });
  render(<PreviewPane {...pane(native)} />);
  await waitFor(() => expect(screen.getByText("pdf:草稿.docx")).toBeTruthy());
  expect(asked("/v1/runs/r1/workspace/preview?path=outputs%2F")).toHaveLength(1);
});

test("a failed workspace render says so; a delivered file falls back to its library preview", async () => {
  routes["/workspace/document"] = () => json({ status: "failed", native: false });
  routes["/workspace/preview"] = () => json({ detail: "渲染失败" }, 422);
  render(<PreviewPane {...pane(native)} />);
  await waitFor(() => expect(screen.getByText(/无法预览/)).toBeTruthy());
  cleanup();
  fetched = [];
  routes = { "/v1/files/f9/document": () => json({ ...ready, native: false }), "/v1/files/f9/preview": () => json({ status: "pending" }), "/v1/files/f9": () => json(file({ id: "f9", filename: "草稿.docx" })) };
  render(<PreviewPane {...pane(native, { "草稿.docx": "f9" })} />);
  await waitFor(() => expect(asked("/v1/files/f9/preview")).toHaveLength(1));
  expect(asked("/workspace/")).toEqual([]);
  await act(async () => {});
});

test("a workspace fallback stops showing the previous version while its replacement loads", async () => {
  routes["/workspace/preview"] = () => json({ filename: "草稿.docx", version: 2, kind: "pdf", pdf: "2/document.pdf" });
  const view = render(<PreviewPane {...pane(native)} onReference={undefined} onAsk={undefined} />);
  await waitFor(() => expect(screen.getByText("pdf:草稿.docx")).toBeTruthy());
  routes["/workspace/preview"] = () => json({ status: "pending" });
  view.rerender(<PreviewPane {...pane({ ...native, version: 3 })} onReference={undefined} onAsk={undefined} />);
  await waitFor(() => expect(screen.queryByText("pdf:草稿.docx")).toBeNull());
  expect(screen.getByRole("status", { name: "正在加载" })).toBeTruthy();
});
