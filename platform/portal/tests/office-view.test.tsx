// @vitest-environment jsdom
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import OfficeView, { docRefOf, VIEW_RENEW_MS, VIEW_REQUEST_MS, type OfficeDoc } from "../components/preview/office-view";
import { api } from "../lib/api";
import { captureView, hasView } from "../lib/office-views";
import { CHANNEL, isChannel, MAX_MESSAGE_CHARS } from "../office/protocol";
import { frameBridge } from "./office-bridge";

vi.mock("../lib/api", () => ({ api: vi.fn(), downloadUrl: (p: string) => p }));
const request = vi.mocked(api);
const doc: OfficeDoc = { path: "库/报告.docx", filename: "报告.docx", kind: "docx", source: { fileId: "f1" } };
const ready = { status: "ready", native: true, version: "hash1", bytes: 3 };
const onReference = vi.fn();
const onAsk = vi.fn().mockResolvedValue(undefined);
function mount(current = doc, conversationId: string | null = null) {
  return render(<OfficeView doc={current} docs={[current]} conversationId={conversationId} onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
}
beforeEach(() => {
  request.mockReset().mockResolvedValue(ready);
  onReference.mockClear(); onAsk.mockClear();
  vi.stubGlobal("React", React);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("manifest.json") ? { ok: true, json: async () => ({ version: "v" }) } : { ok: true, text: async () => `bundle:${url}`, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function bridge(current = doc, conversationId: string | null = null) {
  const view = mount(current, conversationId);
  return { ...view, ...(await frameBridge(current.filename)) };
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
// ---- the viewer giving up after it is up: renderer failures fall back once, source failures say why ----

let fallbacks = 0;
function Fallback() {
  React.useEffect(() => void fallbacks++, []);
  return <div>fallback</div>;
}
async function loaded(conversation: string | null = "c1") {
  fallbacks = 0;
  const view = render(<OfficeView doc={doc} docs={[doc]} conversationId={conversation} onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<Fallback />} />);
  const b = { ...view, ...(await frameBridge(doc.filename)) };
  await b.send({ t: "ready", viewId: "v1" });
  await b.send({ t: "call", id: 1, name: "load_document", args: { path: doc.path } });
  const token = b.reply(1).value.file.token;
  return { ...b, token };
}
const frames = () => document.querySelectorAll("iframe.office-frame").length;

test.each(["load", "render"])("a %s failure of this version after ready unmounts the frame and falls back once", async stage => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const b = await loaded(null);
  await b.send({ t: "bytes", id: 2, token: b.token });
  await b.send({ t: "failed", version: "hash1", stage, message: "renderer could not draw it" });
  expect(frames()).toBe(0);
  expect(screen.getByText("fallback")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  // the same failure again, or a stale one from the closed frame, mounts nothing more
  await b.send({ t: "failed", version: "hash1", stage, message: "again" });
  expect(fallbacks).toBe(1);
});

test("a failure after ready releases the view, rejects pending captures and stops the download", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  onScreen();
  let downloadSignal: AbortSignal | undefined;
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith("/content")) {
      downloadSignal = init!.signal!;
      return new Promise(() => {});
    }
    return base(url, init);
  });
  const b = await loaded("c1");
  await b.send({ t: "call", id: 3, name: "report_view", args: { at: "p:1", selection: null } });
  await waitFor(() => expect(puts()).toHaveLength(1));
  const id = putBody(0).view_id;
  const capture = captureView(id, null)!.catch((error: Error) => error.message);
  await b.send({ t: "failed", version: "hash1", stage: "render", message: "boom" });
  expect(await capture).toBe("查看器已关闭");
  expect(hasView(id)).toBe(false);
  expect(deletes()).toEqual([released("c1", id)]);
  expect(downloadSignal!.aborted).toBe(true);
  expect(screen.getByText("fallback")).toBeTruthy();
});

test("a failure for another version, another nonce or another window is ignored; warnings after ready never stop the viewer", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const b = await loaded(null);
  await b.send({ t: "failed", version: "hash0", stage: "render", message: "old version" });
  await b.emit({ t: "failed", nonce: "old", version: "hash1", stage: "render", message: "old frame" });
  await b.emit({ t: "failed", nonce: b.bundle.nonce, version: "hash1", stage: "render", message: "elsewhere" }, window);
  await b.emit({ t: "error", message: "font warning" });
  expect(frames()).toBe(1);
  expect(fallbacks).toBe(0);
});

test("a failure from a replaced frame cannot stop the new version", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const b = await loaded(null);
  const oldWindow = b.frame.contentWindow;
  const oldNonce = b.bundle.nonce;
  request.mockResolvedValue({ ...ready, version: "hash2" });
  b.rerender(<OfficeView doc={doc} docs={[doc]} revision={1} conversationId={null} onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<Fallback />} />);
  await waitFor(() => expect(screen.getByTitle(doc.filename)).not.toBe(b.frame));
  await b.emit({ t: "failed", nonce: oldNonce, version: "hash1", stage: "render", message: "late" }, oldWindow);
  await b.emit({ t: "failed", nonce: oldNonce, version: "hash2", stage: "render", message: "late" }, oldWindow);
  expect(frames()).toBe(1);
  expect(fallbacks).toBe(0);
});

test.each([
  [404, "文件不存在或无权访问"],
  [403, "文件不存在或无权访问"],
  [409, "文件已更新，请重新打开"],
  [500, "文件加载失败（500）"],
])("an original that answers %i is an explicit error, never the paged preview", async (status, text) => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) => (String(url).endsWith("/content") ? ({ ok: false, status } as Response) : base(url, init)));
  const b = await loaded(null);
  await b.send({ t: "bytes", id: 2, token: b.token });
  expect(b.reply(2)).toMatchObject({ ok: false });
  await b.send({ t: "failed", version: "hash1", stage: "load", message: String(status) });
  expect(screen.getByRole("alert").textContent).toBe(text);
  expect(frames()).toBe(0);
  expect(fallbacks).toBe(0);
});

test("a network failure reading the original, or a changed size, is a source error too", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) => (String(url).endsWith("/content") ? Promise.reject(new TypeError("offline")) : base(url, init)));
  const b = await loaded(null);
  await b.send({ t: "bytes", id: 2, token: b.token });
  await b.send({ t: "failed", version: "hash1", stage: "load", message: "offline" });
  expect(screen.getByRole("alert").textContent).toBe("文件加载失败，请检查网络后重试");
  expect(fallbacks).toBe(0);
  cleanup();
  vi.mocked(fetch).mockImplementation(async (url, init) => (String(url).endsWith("/content") ? ({ ok: true, arrayBuffer: async () => new Uint8Array(5).buffer } as Response) : base(url, init)));
  const changed = await loaded(null);
  await changed.send({ t: "bytes", id: 2, token: changed.token });
  expect(changed.reply(2)).toMatchObject({ ok: false, error: { code: "document_changed" } });
  await changed.send({ t: "failed", version: "hash1", stage: "load", message: "size" });
  expect(screen.getByRole("alert").textContent).toBe("文件已更新，请重新打开");
  expect(fallbacks).toBe(0);
});

test("an encrypted document is said so rather than exported", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const b = await loaded(null);
  await b.send({ t: "failed", version: "hash1", stage: "render", message: "password", code: "password" });
  expect(screen.getByRole("alert").textContent).toBe("文档已加密，无法预览");
  expect(fallbacks).toBe(0);
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
  expect(screen.getByRole("status", { name: "正在加载" })).toBeTruthy();
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
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  b.post.mockClear();
  const capture = captureView(putBody(0).view_id, "p:1")!;
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

test("same native hash refresh preserves an in-flight capture; a new content hash replaces the frame", async () => {
  const b = await reporting();
  const original = screen.getByTitle(doc.filename);
  await b.report(1, "p:1");
  await advance(600);
  b.post.mockClear();
  const capture = captureView(putBody(0).view_id, "p:1")!;
  const tool = b.post.mock.calls.map(([m]) => m).find(m => m.t === "page-tool");
  const props = { doc, docs: [doc], conversationId: "c1", onOpen: vi.fn(), onReference, onAsk, fallback: <div>fallback</div> };
  request.mockResolvedValue({ ...ready });
  const initialCalls = request.mock.calls.length;
  b.rerender(<OfficeView {...props} revision={1} />);
  await advance(0);
  expect(request.mock.calls.length).toBe(initialCalls + 1);
  expect(screen.getByTitle(doc.filename)).toBe(original);
  await b.send({ t: "page-tool-result", id: tool.id, ok: true, value: { content: [{ type: "image", data: "png" }, { type: "text", text: "库/报告.docx#p:1 (800×600 px)" }] } });
  expect(await capture).toMatchObject({ data: "png", version: "hash1" });
  request.mockResolvedValue({ ...ready, version: "changed" });
  b.rerender(<OfficeView {...props} revision={2} />);
  await advance(0);
  await advance(0);
  expect(screen.getByTitle(doc.filename)).not.toBe(original);
});

// ---- loading: one placeholder from the index wait until the viewer asks for its document ----

test("the placeholder covers the frame until the viewer loads its document, and returns for a new content version", async () => {
  const b = await bridge();
  const placeholder = screen.getByRole("status", { name: "正在加载" });
  expect(placeholder.dataset.shape).toBe("page");
  await b.send({ t: "ready", viewId: "loading-view" });
  expect(screen.getByRole("status", { name: "正在加载" })).toBe(placeholder);
  await b.send({ t: "call", id: 1, name: "load_document", args: { path: doc.path } });
  expect(screen.queryByRole("status", { name: "正在加载" })).toBeNull();
  request.mockResolvedValue({ ...ready, version: "hash2" });
  b.rerender(<OfficeView doc={doc} docs={[doc]} revision={1} onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
  expect(await screen.findByRole("status", { name: "正在加载" })).toBeTruthy();
});

test.each([["xlsx", "sheet"], ["pptx", "slide"], ["pdf", "page"]] as const)("a %s index wait draws a %s", async (kind, shape) => {
  request.mockReturnValue(new Promise(() => {}));
  mount({ ...doc, kind, filename: `shape.${kind}` });
  expect(screen.getByRole("status", { name: "正在加载" }).dataset.shape).toBe(shape);
});

// ---- the reported view: renewed while on screen, quiet while hidden, released when closed ----

let visibility: DocumentVisibilityState = "visible";
let displayed = true;
function onScreen() {
  visibility = "visible";
  displayed = true;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  vi.spyOn(HTMLIFrameElement.prototype, "getClientRects").mockImplementation(() => (displayed ? [{}] : []) as unknown as DOMRectList);
}
async function setVisibility(value: DocumentVisibilityState) {
  visibility = value;
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
}
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
const puts = (conversation = "c1") => request.mock.calls.filter(([url, init]) => url === `/v1/conversations/${conversation}/view` && init?.method === "PUT");
const putBody = (n: number, conversation = "c1") => JSON.parse(String(puts(conversation)[n][1]!.body));
const report = (n: number, conversation = "c1") => { const { sequence, ...rest } = putBody(n, conversation); expect(Number.isSafeInteger(sequence)).toBe(true); return rest; };
const sequences = () => request.mock.calls.filter(([, init]) => init?.method === "PUT").map(([, init]) => JSON.parse(String(init!.body)).sequence as number);
const released = (conversation: string, id: string) => `/v1/conversations/${conversation}/view?view_id=${id}`;
const deletes = () => vi.mocked(fetch).mock.calls.map(([url, init]) => [String(url), (init as RequestInit | undefined)?.method]).filter(([, method]) => method === "DELETE").map(([url]) => url);
async function reporting(conversation: string | null = "c1") {
  onScreen();
  const b = await bridge(doc, conversation);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  await b.send({ t: "ready", viewId: "v1" });
  const report = (id: number, at: string, text?: string) =>
    b.send({ t: "call", id, name: "report_view", args: { at, selection: text ? { at, text } : null } });
  return { ...b, report };
}

test("an open view renews its last report, any report restarts the interval, and nothing else is sent", async () => {
  const b = await reporting();
  await b.report(1, "p:2", "选中的句子");
  await advance(599);
  expect(puts()).toHaveLength(0);
  await advance(1);
  const id = putBody(0).view_id;
  expect(report(0)).toEqual({ view_id: id, file_id: "f1", version: "hash1", at: "p:2", selection: { at: "p:2", text: "选中的句子" } });
  await advance(VIEW_RENEW_MS - 1);
  expect(puts()).toHaveLength(1);
  await advance(1);
  expect(puts()).toHaveLength(2);
  // a renewal is the same report under the same id, with a newer sequence
  expect(report(1)).toEqual(report(0));
  expect(putBody(1).sequence).toBeGreaterThan(putBody(0).sequence);
  await advance(60_000);
  await b.report(2, "p:3");
  await advance(600);
  expect(putBody(2)).toMatchObject({ at: "p:3", selection: null });
  await advance(VIEW_RENEW_MS - 1);
  expect(puts()).toHaveLength(3);
  await advance(1);
  expect(puts()).toHaveLength(4);
  // only view reports: no other API call, nothing posted back into the frame but call replies
  expect(request.mock.calls.filter(([url]) => !String(url).endsWith("/view"))).toHaveLength(1);
});

test("a hidden page stops renewing; coming back reports at once unless it just reported; closing releases", async () => {
  const b = await reporting();
  await b.report(1, "p:2", "选中");
  await advance(600);
  await setVisibility("hidden");
  await advance(VIEW_RENEW_MS * 4);
  expect(puts()).toHaveLength(1);
  await setVisibility("visible");
  await advance(0);
  expect(puts()).toHaveLength(2);
  expect(report(1)).toEqual(report(0));
  // a quick switch away and back does not report again; the renewal keeps its schedule
  await setVisibility("hidden");
  await setVisibility("visible");
  await advance(0);
  expect(puts()).toHaveLength(2);
  await advance(VIEW_RENEW_MS);
  expect(puts()).toHaveLength(3);
  b.unmount();
  expect(deletes()).toEqual([released("c1", putBody(0).view_id)]);
  await advance(VIEW_RENEW_MS * 3);
  expect(puts()).toHaveLength(3);
});

test("a report made just before the page is hidden waits for it to come back, then goes at once", async () => {
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  await b.report(2, "p:2", "隐藏前选中");
  await setVisibility("hidden");
  await advance(VIEW_RENEW_MS * 2);
  expect(puts()).toHaveLength(1);
  // the last report is recent, but a newer one waited: it is sent on return
  await setVisibility("visible");
  await advance(0);
  expect(puts()).toHaveLength(2);
  expect(putBody(1)).toMatchObject({ at: "p:2", selection: { text: "隐藏前选中" } });
  await advance(VIEW_RENEW_MS);
  expect(puts()).toHaveLength(3);
  b.unmount();
});

test("a mounted but undisplayed view does not renew until it is displayed again", async () => {
  const b = await reporting();
  await b.report(1, "p:2");
  await advance(600);
  displayed = false;
  await advance(VIEW_RENEW_MS * 2);
  expect(puts()).toHaveLength(1);
  displayed = true;
  await advance(VIEW_RENEW_MS);
  expect(puts()).toHaveLength(2);
  b.unmount();
});

test("one request at a time: reports during a slow request collapse into the latest; a failure waits for the next renewal", async () => {
  const b = await reporting();
  let finish!: (value: unknown) => void;
  request.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
  await b.report(1, "p:1");
  await advance(600);
  expect(puts()).toHaveLength(1);
  await b.report(2, "p:2");
  await advance(600);
  await b.report(3, "p:3");
  await advance(600);
  expect(puts()).toHaveLength(1);
  request.mockRejectedValueOnce(new Error("offline"));
  await act(async () => finish({ status: "ready" }));
  await advance(0);
  expect(puts()).toHaveLength(2);
  expect(putBody(1).at).toBe("p:3");
  // the failed report is not retried in a loop
  await advance(VIEW_RENEW_MS - 1);
  expect(puts()).toHaveLength(2);
  await advance(1);
  expect(puts()).toHaveLength(3);
  b.unmount();
});

test("a report that never answers is abandoned at its deadline; later reports and renewals continue", async () => {
  const b = await reporting();
  let signal: AbortSignal | undefined;
  // a real fetch rejects the moment it is aborted, while the server may still be working on it
  request.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
    signal = init!.signal!;
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }));
  await b.report(1, "p:1");
  await advance(600);
  await b.report(2, "p:2");
  await advance(600);
  expect(puts()).toHaveLength(1);
  await advance(VIEW_REQUEST_MS - 600 - 1); // it started 600 ms ago
  expect(signal!.aborted).toBe(false);
  await advance(1);
  expect(signal!.aborted).toBe(true);
  await advance(0);
  // the newer report waited behind it and goes now, ordered after the abandoned one; renewals resume after that
  expect(puts()).toHaveLength(2);
  expect(putBody(1)).toMatchObject({ at: "p:2", view_id: putBody(0).view_id });
  expect(putBody(1).sequence).toBeGreaterThan(putBody(0).sequence);
  await advance(VIEW_RENEW_MS);
  expect(puts()).toHaveLength(3);
  b.unmount();
});

test("every report on the page has a larger sequence, across renewals, aborts, rebinding and new frames", async () => {
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  await advance(VIEW_RENEW_MS);
  await b.send({ t: "call", id: 2, name: "release_view", args: {} });
  await b.report(3, "p:2");
  await advance(600);
  b.unmount();
  vi.useRealTimers();
  const again = await reporting();
  await again.report(1, "p:3");
  await advance(600);
  const seen = sequences();
  expect(seen).toHaveLength(4);
  expect(seen.every((value, n) => n === 0 || value > seen[n - 1])).toBe(true);
  again.unmount();
});

test("closing while a report is on its way releases the view once; the abandoned report changes nothing after", async () => {
  const b = await reporting();
  let finish!: (value: unknown) => void;
  let signal: AbortSignal | undefined;
  // the server took the request; abort settles the fetch at once on the client side only
  request.mockImplementationOnce((_url, init) => ((signal = init!.signal!), new Promise((resolve) => (finish = resolve))));
  await b.report(1, "p:1");
  await advance(600);
  const id = putBody(0).view_id;
  b.unmount();
  expect(signal!.aborted).toBe(true);
  expect(deletes()).toEqual([released("c1", id)]);
  expect(hasView(id)).toBe(false);
  await act(async () => finish({ status: "ready" }));
  await advance(VIEW_RENEW_MS * 2);
  expect(deletes()).toHaveLength(1);
  expect(puts()).toHaveLength(1);
});

test("a released view reports again under a new id; captures follow the id that is reported", async () => {
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  const first = putBody(0).view_id;
  expect(hasView(first)).toBe(true);
  await b.send({ t: "call", id: 2, name: "release_view", args: {} });
  expect(deletes()).toEqual([released("c1", first)]);
  expect(hasView(first)).toBe(false);
  await b.report(3, "p:2");
  await advance(600);
  const second = putBody(1).view_id;
  expect(second).not.toBe(first);
  expect(hasView(second)).toBe(true);
  // the frame announcing itself again changes nothing
  await b.send({ t: "ready", viewId: "v1" });
  await advance(VIEW_RENEW_MS);
  expect(putBody(2).view_id).toBe(second);
  b.unmount();
  expect(deletes()).toEqual([released("c1", first), released("c1", second)]);
});

test("switching conversations A→B→A releases each binding and never reuses a released id", async () => {
  const b = await reporting("A");
  const props = { doc, docs: [doc], onOpen: vi.fn(), onReference, onAsk, fallback: <div>fallback</div> };
  await b.report(1, "p:1");
  await advance(600);
  const a1 = putBody(0, "A").view_id;
  b.rerender(<OfficeView {...props} conversationId="B" />);
  await advance(0);
  const b1 = putBody(0, "B").view_id;
  b.rerender(<OfficeView {...props} conversationId="A" />);
  await advance(0);
  const a2 = putBody(1, "A").view_id;
  expect(new Set([a1, b1, a2]).size).toBe(3);
  expect(deletes()).toEqual([released("A", a1), released("B", b1)]);
  expect(hasView(a1)).toBe(false);
  expect(hasView(b1)).toBe(false);
  expect(hasView(a2)).toBe(true);
  b.unmount();
});

test("switching conversations during a stuck report releases the old binding at once and reports to the new one after", async () => {
  const b = await reporting("A");
  const props = { doc, docs: [doc], onOpen: vi.fn(), onReference, onAsk, fallback: <div>fallback</div> };
  request.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => init!.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))));
  await b.report(1, "p:1");
  await advance(600);
  const a1 = putBody(0, "A").view_id;
  b.rerender(<OfficeView {...props} conversationId="B" />);
  await advance(0);
  expect(deletes()).toEqual([released("A", a1)]);
  expect(puts("B")).toHaveLength(0);
  await advance(VIEW_REQUEST_MS);
  expect(puts("B")).toHaveLength(1);
  expect(putBody(0, "B").view_id).not.toBe(a1);
  b.unmount();
});

test("the viewer releasing its view stops the renewal", async () => {
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  await b.send({ t: "call", id: 2, name: "release_view", args: {} });
  expect(deletes()).toEqual([released("c1", putBody(0).view_id)]);
  await advance(VIEW_RENEW_MS * 3);
  expect(puts()).toHaveLength(1);
  b.unmount();
  expect(deletes()).toHaveLength(1);
});

test("a new content version releases the old view and its renewal; the new frame reports on its own", async () => {
  const b = await reporting();
  await b.report(1, "p:1");
  await advance(600);
  request.mockResolvedValue({ ...ready, version: "hash2" });
  b.rerender(<OfficeView doc={doc} docs={[doc]} revision={1} conversationId="c1" onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
  await advance(0);
  await advance(0);
  expect(deletes()).toEqual([released("c1", putBody(0).view_id)]);
  await advance(VIEW_RENEW_MS * 2);
  expect(puts()).toHaveLength(1);
  b.unmount();
});

test("a view reported before the conversation existed is reported once it does", async () => {
  const b = await reporting(null);
  await b.report(1, "p:2");
  await advance(600);
  expect(request.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
  b.rerender(<OfficeView doc={doc} docs={[doc]} conversationId="c2" onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
  await advance(0);
  expect(puts("c2")).toHaveLength(1);
  b.rerender(<OfficeView doc={doc} docs={[doc]} conversationId="c3" onOpen={vi.fn()} onReference={onReference} onAsk={onAsk} fallback={<div>fallback</div>} />);
  await advance(0);
  expect(deletes()).toEqual([released("c2", putBody(0, "c2").view_id)]);
  expect(puts("c3")).toHaveLength(1);
  expect(putBody(0, "c3").view_id).not.toBe(putBody(0, "c2").view_id);
  b.unmount();
});
