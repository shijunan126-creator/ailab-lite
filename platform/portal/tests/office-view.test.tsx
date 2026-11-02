// @vitest-environment jsdom
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import OfficeView, { docRefOf, type OfficeDoc } from "../components/preview/office-view";
import { api } from "../lib/api";
import { captureView } from "../lib/office-views";
import { CHANNEL, isChannel, MAX_MESSAGE_CHARS } from "../office/protocol";

vi.mock("../lib/api", () => ({ api: vi.fn(), downloadUrl: (p: string) => p }));
const request = vi.mocked(api);
const doc: OfficeDoc = { path: "库/报告.docx", filename: "报告.docx", kind: "docx", source: { fileId: "f1" } };
const ready = { status: "ready", native: true, version: "hash1", bytes: 3 };
const onReference = vi.fn();
const onAsk = vi.fn().mockResolvedValue(undefined);
function mount(current = doc) {
  return render(<OfficeView doc={current} docs={[current]} onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
}
beforeEach(() => {
  request.mockReset().mockResolvedValue(ready);
  onReference.mockClear(); onAsk.mockClear();
  vi.stubGlobal("React", React);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("manifest.json") ? { ok: true, json: async () => ({ version: "v" }) } : { ok: true, text: async () => `bundle:${url}`, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function bridge(current = doc) {
  const view = mount(current);
  const frame = await screen.findByTitle(current.filename) as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
  const emit = async (data: object, source = frame.contentWindow) => {
    await act(async () => { window.dispatchEvent(new MessageEvent("message", { source, data: { ch: CHANNEL, ...data } })); });
  };
  await emit({ t: "boot" });
  await waitFor(() => expect(post.mock.calls.some(([m]) => m.t === "bundle")).toBe(true));
  const bundle = post.mock.calls.find(([m]) => m.t === "bundle")![0];
  const nonce = bundle.nonce;
  const send = (data: object) => emit({ nonce, ...data });
  const reply = (id: number) => post.mock.calls.map(([m]) => m).find(m => m.t === "reply" && m.id === id);
  post.mockClear();
  return { ...view, post, send, emit, reply, bundle };
}
test("channel accepts only typed envelopes", () => {
  for (const value of [null, undefined, 1, "x", {}, { ch: "wrong", t: "call" }, { ch: CHANNEL, t: 1 }]) expect(isChannel(value)).toBe(false);
  expect(isChannel({ ch: CHANNEL, t: "call" })).toBe(true);
});
test("parent ignores wrong source, channel, nonce and oversized calls", async () => {
  const b = await bridge();
  await b.emit({ t: "call", nonce: "wrong", id: 1, name: "get_viewer", args: {} });
  await b.emit({ ch: "wrong", t: "error" });
  await b.send({ t: "call", id: 2, name: "get_viewer", args: { padding: "x".repeat(MAX_MESSAGE_CHARS) } });
  await act(async () => { window.dispatchEvent(new MessageEvent("message", { source: window, data: { ch: CHANNEL, t: "error" } })); });
  await b.emit({ t: "error", message: "x".repeat(MAX_MESSAGE_CHARS) });
  expect(b.post).not.toHaveBeenCalled();
  expect(screen.queryByText("fallback")).toBeNull();
});
test("load path is bound to this document; bytes token transfers once; unknown calls fail", async () => {
  const b = await bridge();
  await b.send({ t: "call", id: 1, name: "load_document", args: { path: "other" } });
  expect(b.reply(1)).toMatchObject({ ok: false, error: { code: "not_found" } });
  await b.send({ t: "call", id: 2, name: "load_document", args: { path: doc.path } });
  const token = b.reply(2).value.file.token;
  await b.send({ t: "bytes", id: 3, token });
  const transfer = b.post.mock.calls.find(([m]) => m.id === 3)!;
  expect(Array.from(new Uint8Array(transfer[0].value))).toEqual([1, 2, 3]);
  expect((transfer as unknown as [unknown, string, Transferable[]])[2]).toEqual([transfer[0].value]);
  await b.send({ t: "bytes", id: 4, token });
  expect(b.reply(4)).toMatchObject({ ok: false });
  await b.send({ t: "call", id: 5, name: "write_file", args: {} });
  expect(b.reply(5)).toMatchObject({ ok: false, error: { code: "invalid_path" } });
});
test.each([doc, { ...doc, source: { runId: "run", path: "输出/报告.docx" } }])("reference and ask freeze exact identity/version for $source", async current => {
  const b = await bridge(current);
  const reference = { path: current.path, at: "p:3", text: "引文" };
  const expected = "fileId" in current.source ? { file_id: "f1", version: "hash1", at: "p:3", quote: "引文" } : { run_id: "run", path: "输出/报告.docx", version: "hash1", at: "p:3", quote: "引文" };
  await b.send({ t: "reference", id: 1, reference });
  expect(onReference).toHaveBeenCalledWith(expected, "报告.docx · p:3");
  await b.send({ t: "ask", id: 2, question: "说明", reference });
  expect(onAsk).toHaveBeenCalledWith("说明", expected, "报告.docx · p:3");
  expect(docRefOf(current, "hash1", { at: "p:3", text: "长".repeat(4001) }).quote).toHaveLength(4000);
  await b.send({ t: "reference", id: 3, reference: { ...reference, path: "other" } });
  expect(b.reply(3)).toMatchObject({ ok: false });
  expect(onReference).toHaveBeenCalledTimes(1);
});
test("frame error before ready falls back", async () => {
  const b = await bridge();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  await b.emit({ t: "error", message: "bundle failed" });
  expect(screen.getByText("fallback")).toBeTruthy();
});
test.each([{ ...ready, native: false }, { ...ready, bytes: 64 * 1024 * 1024 + 1 }, { ...ready, status: "failed" }, { ...ready, version: null }])("index falls back for $status/$native/$bytes/$version", async value => {
  request.mockResolvedValue(value); mount();
  expect(await screen.findByText("fallback")).toBeTruthy();
});
test("index request failure falls back", async () => {
  request.mockRejectedValue(new Error("offline")); mount();
  expect(await screen.findByText("fallback")).toBeTruthy();
});
test("pending index polls then opens native; unmount cancels polling", async () => {
  vi.useFakeTimers();
  request.mockResolvedValueOnce({ status: "pending" }).mockResolvedValueOnce(ready);
  const view = mount();
  await act(async () => {});
  expect(screen.getByRole("status").textContent).toBe("正在加载");
  await act(async () => { await vi.advanceTimersByTimeAsync(1499); });
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByTitle(doc.filename)).toBeTruthy();
  view.unmount();
  expect(request.mock.calls[0][1]!.signal!.aborted).toBe(true);
  request.mockReset().mockResolvedValue({ status: "pending" });
  const pending = mount(); await act(async () => {}); pending.unmount();
  await vi.advanceTimersByTimeAsync(3000);
  expect(request).toHaveBeenCalledTimes(1);
});

test("capture results above the control-message limit still reach the caller", async () => {
  const b = await bridge();
  await b.send({ t: "ready", viewId: "capture-view" });
  const capture = captureView("capture-view", "p:1")!;
  const tool = b.post.mock.calls.map(([m]) => m).find(m => m.t === "page-tool");
  const data = "x".repeat(MAX_MESSAGE_CHARS + 1);
  await b.send({ t: "page-tool-result", id: tool.id, ok: true, value: { content: [{ type: "image", data }, { type: "text", text: "库/报告.docx#p:1 (800×600 px)" }] } });
  const outcome = await Promise.race([capture, Promise.resolve("dropped")]);
  expect(outcome).toEqual({ data, width: 800, height: 600, at: "p:1", version: "hash1" });
});

test.each(["docx", "xlsx", "pptx", "pdf"] as const)("boot only requests the %s renderer", async kind => {
  // A distinct filename avoids the module-level asset cache from earlier bridge tests.
  const current = { ...doc, kind, filename: `format.${kind}` };
  const b = await bridge(current);
  const calls = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
  expect(calls.some(url => /viewer\.js$/.test(url))).toBe(false);
  expect(calls.filter(url => /viewer-.*\.js$/.test(url)).every(url => url.endsWith(`viewer-${kind}.js`))).toBe(true);
  expect(b.bundle.js).toContain(`viewer-${kind}.js`);
});
