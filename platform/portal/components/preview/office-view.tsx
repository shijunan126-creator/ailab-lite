"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, downloadUrl, type DocRef, type DocumentIndex, type OfficeKind } from "../../lib/api";
import { registerView, type OfficeCapture } from "../../lib/office-views";
import DocLoading, { type LoadingShape } from "./doc-loading";
import { DocTools } from "./doc-tools";
import { IconPages, IconSearch } from "../icons";
import { CHANNEL, isChannel, MAX_CAPTURE_MESSAGE_CHARS, MAX_MESSAGE_CHARS, type FrameMessage, type HostMessage, type ViewerCommand, type ViewerReference, type ViewerTools } from "../../office/protocol";

/** A document the side pane can open natively: a library file, or a file still in a Work run's workspace. */
export type OfficeDoc = {
  /** The viewer's key for it ("库/报告.docx"): shown in the viewer and used in its anchors. */
  path: string;
  filename: string;
  kind: OfficeKind;
  /** A library file (through a share when `shareId` is set), or a file still in a Work run's workspace. */
  source: { fileId: string; shareId?: string } | { runId: string; path: string };
};

export type OfficeReveal = { seq: number; targets: { at: string; quote?: string }[]; note?: string | null };

type Outgoing = HostMessage extends infer T ? (T extends unknown ? Omit<T, "ch"> : never) : never;

const POLL_MS = 1500;
/** The API forgets a view 600 s after its last report. A view still on screen renews well inside that; any report resets it. */
export const VIEW_RENEW_MS = 180_000;
/** Coming back to the page reports at once unless the last report is this recent. */
const VIEW_RESUME_MS = 60_000;
/** The viewer reports changes after its own 400 ms pause; this coalesces bursts of them into one request. */
const VIEW_DEBOUNCE_MS = 600;
/** A view report that has not answered by then is abandoned, so a stuck connection cannot hold back the next one. */
export const VIEW_REQUEST_MS = 15_000;
const SHAPES: Record<OfficeKind, LoadingShape> = { docx: "page", pdf: "page", pptx: "slide", xlsx: "sheet" };
/** Above this the browser would hold several copies of a large file; the paged preview shows it instead. */
export const MAX_NATIVE_BYTES = 64 * 1024 * 1024;
const EXTENSIONS: Record<string, OfficeKind> = { docx: "docx", xlsx: "xlsx", pptx: "pptx", pdf: "pdf" };

/** Every view report on this page carries a larger sequence than the last, whatever view or conversation it is for:
 * the API orders a view's reports by it, so a report abandoned at its deadline can never land over a newer one. */
let reportSequence = 0;
/** Why the source itself cannot be shown; the paged preview would fail for the same reason, so none is started. */
function sourceError(status: number | null): string {
  if (status === 401 || status === 403 || status === 404) return "文件不存在或无权访问";
  if (status === 409 || status === 410) return "文件已更新，请重新打开";
  return status ? `文件加载失败（${status}）` : "文件加载失败，请检查网络后重试";
}

export function officeKind(filename: string): OfficeKind | null {
  return EXTENSIONS[filename.toLowerCase().match(/\.([a-z]+)$/)?.[1] ?? ""] ?? null;
}

const fileBase = (source: { fileId: string; shareId?: string }) =>
  source.shareId ? `/v1/shares/${source.shareId}/files/${source.fileId}` : `/v1/files/${source.fileId}`;
const indexUrl = (source: OfficeDoc["source"]) =>
  "fileId" in source ? `${fileBase(source)}/document` : `/v1/runs/${source.runId}/workspace/document?path=${encodeURIComponent(source.path)}`;
const contentUrl = (source: OfficeDoc["source"]) =>
  downloadUrl("fileId" in source ? `${fileBase(source)}/content` : `/v1/runs/${source.runId}/workspace/content?path=${encodeURIComponent(source.path)}`);

/** The reference the API will check: the document's identity plus the exact version the user looked at. */
export function docRefOf(doc: OfficeDoc, version: string, ref: { at: string; text: string }): DocRef {
  const quote = ref.text.trim() ? ref.text.slice(0, 4000) : "";
  return "fileId" in doc.source
    ? { file_id: doc.source.fileId, version, at: ref.at, quote }
    : { run_id: doc.source.runId, path: doc.source.path, version, at: ref.at, quote };
}

// Viewer files are fetched once per page: the version path makes them immutable.
let assetVersion: Promise<string> | undefined;
const assetCache = new Map<string, Promise<string | ArrayBuffer>>();
const assetSizes = new Map<string, number>();
const ASSET_CACHE_BYTES = 16 * 1024 * 1024;
function trimAssets() {
  let bytes = Array.from(assetSizes.values()).reduce((sum, size) => sum + size, 0);
  while (assetCache.size > 64 || bytes > ASSET_CACHE_BYTES) {
    const oldest = assetCache.keys().next().value;
    if (!oldest) break;
    bytes -= assetSizes.get(oldest) ?? 0;
    assetCache.delete(oldest);
    assetSizes.delete(oldest);
  }
}
function asset(name: string, binary: boolean): Promise<string | ArrayBuffer> {
  const key = `${binary ? "b" : "t"}:${name}`;
  let cached = assetCache.get(key);
  if (cached) {
    assetCache.delete(key);
    assetCache.set(key, cached);
  }
  if (!cached) {
    cached = (assetVersion ??= fetch("/office-viewer/current/manifest.json", { cache: "no-store", signal: AbortSignal.timeout(15000) })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`查看器不可用 ${r.status}`))))
      .then((m: { version: string }) => m.version))
      .then((version) => fetch(`/office-viewer/${version}/${name.split("/").map(encodeURIComponent).join("/")}`, { signal: AbortSignal.timeout(15000) }))
      .then((r) => (r.ok ? (binary ? r.arrayBuffer() : r.text()) : Promise.reject(new Error(`${name} ${r.status}`))));
    cached.then((value) => {
      if (assetCache.get(key) === cached) { assetSizes.set(key, typeof value === "string" ? value.length * 2 : value.byteLength); trimAssets(); }
    }, () => undefined);
    cached.catch(() => {
      assetCache.delete(key);
      assetSizes.delete(key);
      assetVersion = undefined;
    });
    assetCache.set(key, cached);
    trimAssets();
  }
  return cached;
}

function useDark() {
  const [dark, setDark] = useState(() => typeof document !== "undefined" && document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const html = document.documentElement;
    const observer = new MutationObserver(() => setDark(html.classList.contains("dark")));
    observer.observe(html, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** The viewer index of a document, polled while it is being made; null until known, "fallback" when not native. */
function useDocumentIndex(doc: OfficeDoc, revision?: number): DocumentIndex | "fallback" | null {
  const [index, setIndex] = useState<DocumentIndex | "fallback" | null>(null);
  const key = JSON.stringify(doc.source);
  const previousKey = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    // A rendered-preview revision can change without the native file changing. Recheck in place;
    // only a different document identity blanks the viewer while its index is loading.
    if (previousKey.current !== key) setIndex(null);
    previousKey.current = key;
    const ask = () =>
      api<DocumentIndex>(indexUrl(doc.source), { signal: controller.signal })
        .then((value) => {
          if (controller.signal.aborted) return;
          if (value.status === "pending") timer = setTimeout(ask, POLL_MS);
          else if (value.status !== "ready" || !value.native || !value.version || (value.bytes ?? 0) > MAX_NATIVE_BYTES) setIndex("fallback");
          else setIndex(value);
        })
        .catch(() => !controller.signal.aborted && setIndex("fallback"));
    ask();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, revision]);
  return index;
}

type Props = {
  doc: OfficeDoc;
  revision?: number;
  /** Every document open in the pane, for the viewer's switch menu; just this one when absent. */
  docs?: OfficeDoc[];
  conversationId?: string | null;
  reveal?: OfficeReveal | null;
  onOpen?: (path: string) => void;
  /** Add the selection to the composer as a quoted reference. Without it and onAsk (no conversation to quote
   * into: the library, a share) the viewer offers no selection actions and reports no view. */
  onReference?: (ref: DocRef, label: string) => void;
  /** Send a question about the selection right away. */
  onAsk?: (question: string, ref: DocRef, label: string) => Promise<void>;
  /** Shown when the document cannot be viewed natively (too large, unsupported, index failed). */
  fallback: ReactNode;
};

/** The native Word/Excel/PowerPoint/PDF viewer in a sandboxed frame (app/frame/office), driven over postMessage. */
export default function OfficeView({ doc, revision, docs: given, conversationId, reveal, onOpen, onReference, onAsk, fallback }: Props) {
  const docs = given ?? [doc];
  const chat = !!(onReference && onAsk);
  const loaded = useDocumentIndex(doc, revision);
  const indexedVersion = loaded && loaded !== "fallback" ? loaded.version : null;
  // The viewer gave up on this version: its bundle did not start or the renderer failed (the paged preview takes
  // over, once per version), or the source itself could not be read (an error, no expensive preview).
  const [stopped, setStopped] = useState<{ version: string | null | undefined; error?: string } | null>(null);
  const stop = stopped && stopped.version === indexedVersion ? stopped : null;
  const index = stop ? "fallback" : loaded;
  const dark = useDark();
  const frame = useRef<HTMLIFrameElement>(null);
  const live = useRef({ doc, docs, conversationId, chat, reveal, onOpen, onReference, onAsk, dark, index });
  live.current = { doc, docs, conversationId, chat, reveal, onOpen, onReference, onAsk, dark, index };
  const channel = useRef<{ up: boolean; send(message: Outgoing, transfer?: Transferable[]): void; report(): void } | null>(null);
  // Until the viewer asks for its document, its frame is blank: the placeholder stays over it till then.
  const [framed, setFramed] = useState<string | null>(null);
  // The viewer's document controls, as it last reported them for the version on screen.
  const [tools, setTools] = useState<{ version: string; tools: ViewerTools } | null>(null);
  const sentReveal = useRef(0);
  const ready = index !== null && index !== "fallback";
  const contentVersion = ready ? index.version : null;

  useEffect(() => {
    const element = frame.current;
    if (!ready || !element) return;
    const nonce = crypto.randomUUID();
    const bytes = new Map<string, Promise<ArrayBuffer>>();
    let structuralBlocks: Promise<unknown[]> | null = null;
    const loadBlocks = () => (structuralBlocks ??= (async () => {
      const source = live.current.doc.source;
      const url = indexUrl(source) + (indexUrl(source).includes("?") ? "&" : "?") + "structure=true";
      const deadline = Date.now() + 30_000;
      for (;;) {
        const result = await api<DocumentIndex>(url, { signal: downloads.signal });
        if (closed || downloads.signal.aborted || result.version !== version) throw new Error("文档已切换");
        if (result.status === "ready") return result.blocks ?? [];
        if (result.status !== "pending" || Date.now() >= deadline) throw new Error("无法核验文档位置");
        await new Promise<void>((resolve, reject) => {
          const abort = () => { clearTimeout(timer); reject(new Error("文档已关闭")); };
          const timer = setTimeout(() => { downloads.signal.removeEventListener("abort", abort); resolve(); }, 1000);
          downloads.signal.addEventListener("abort", abort, { once: true });
        });
      }
    })());
    // Closing or switching the document stops a download the viewer has not taken yet.
    const downloads = new AbortController();
    const captures = new Map<number, { resolve(value: OfficeCapture): void; reject(error: Error): void; dispose(): void }>();
    let toolSeq = 0;
    let up = false;
    // Set when the document's bytes could not be read: a failure the viewer then reports is the source's, not its own.
    let unreadable: string | null = null;
    const version = contentVersion;
    const send = (message: Outgoing, transfer: Transferable[] = []) =>
      !closed && element.contentWindow?.postMessage({ ch: CHANNEL, ...message }, "*", transfer);
    const capture = (at: string | null, maxWidth: number, signal?: AbortSignal) =>
      new Promise<OfficeCapture>((resolve, reject) => {
        if (closed || signal?.aborted) return reject(new Error("查看器已关闭"));
        if (captures.size) return reject(new Error("截图正在处理中"));
        const id = ++toolSeq;
        const finish = (error: string) => {
          const waiter = captures.get(id);
          if (!waiter) return;
          captures.delete(id);
          waiter.dispose();
          send({ t: "page-tool-cancel", id });
          reject(new Error(error));
        };
        const abort = () => finish("截图已取消");
        const timer = setTimeout(() => finish("截图超时"), 15_000);
        const dispose = () => {
          clearTimeout(timer);
          signal?.removeEventListener("abort", abort);
        };
        captures.set(id, { resolve, reject, dispose });
        signal?.addEventListener("abort", abort, { once: true });
        send({ t: "page-tool", id, name: "capture_view", args: { ...(at ? { anchor: `${live.current.doc.path}#${at}` } : {}), maxWidth } });
      });

    // What the user is looking at, for the model's get_view and viewport captures. The API keeps it 600 s, so
    // a view left open on a visible page is renewed every few minutes with its last report. A hidden page sends
    // nothing (its state lapses; a report made just before hiding waits), coming back reports at once when
    // something waits or the last report is not recent, and closing the view releases it. One request at a
    // time: reports that arrive meanwhile collapse into the latest, and a failure waits for the next renewal.
    //
    // A view is reported under a binding: an id of the Portal's own, for one conversation, until it is released.
    // The API closes a released id for good (a report still on its way can no longer land), so reporting again,
    // or to another conversation, starts a new binding; the capture registry follows the binding's id.
    let report: Record<string, unknown> | null = null;
    let binding: { id: string; conversation: string; unregister(): void } | null = null;
    let sentAt = Number.NEGATIVE_INFINITY;
    let inflight: AbortController | null = null;
    let again = false;
    let held = false;
    let closed = false;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    let renewal: ReturnType<typeof setTimeout> | undefined;
    const releaseView = () => {
      if (!binding) return;
      const { id, conversation, unregister } = binding;
      binding = null;
      unregister();
      void fetch(downloadUrl(`/v1/conversations/${conversation}/view?view_id=${encodeURIComponent(id)}`), { method: "DELETE", keepalive: true }).catch(() => undefined);
    };
    const renewIn = (ms: number) => {
      clearTimeout(renewal);
      if (report && !closed && document.visibilityState === "visible") renewal = setTimeout(renew, ms);
    };
    // A pane tab that is mounted but not displayed is not being looked at.
    const renew = () => (element.getClientRects().length ? flush() : renewIn(VIEW_RENEW_MS));
    const flush = () => {
      clearTimeout(debounce);
      clearTimeout(renewal);
      const conversation = live.current.chat ? live.current.conversationId : null;
      if (closed || !report || !up) return;
      // The pane moved to another conversation (or to none): the view leaves the old one at once.
      if (binding && binding.conversation !== conversation) releaseView();
      if (!conversation) return;
      if (document.visibilityState !== "visible") return void (held = true);
      held = false;
      if (inflight) return void (again = true);
      if (!binding) {
        const id = crypto.randomUUID();
        binding = { id, conversation, unregister: registerView(id, capture) };
      }
      const request = (inflight = new AbortController());
      const deadline = setTimeout(() => request.abort(), VIEW_REQUEST_MS);
      const body = { view_id: binding.id, sequence: ++reportSequence, ...report };
      void api(`/v1/conversations/${conversation}/view`, { method: "PUT", body: JSON.stringify(body), signal: request.signal })
        .then(() => void (sentAt = Date.now()), () => undefined)
        .finally(() => {
          clearTimeout(deadline);
          inflight = null;
          if (closed || !report) return;
          if (again) {
            again = false;
            flush();
          } else renewIn(VIEW_RENEW_MS);
        });
    };
    const putView = (args: Record<string, unknown>) => {
      const selection = args.selection as { at: string; text: string } | null;
      const source = live.current.doc.source;
      report = {
        ...("fileId" in source ? { file_id: source.fileId } : { run_id: source.runId, path: source.path }),
        version: (live.current.index as DocumentIndex).version,
        at: typeof args.at === "string" ? args.at : null,
        selection: selection ? { at: selection.at, text: selection.text.slice(0, 4000) } : null,
      };
      clearTimeout(debounce);
      debounce = setTimeout(flush, VIEW_DEBOUNCE_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return clearTimeout(renewal);
      if (held || Date.now() - sentAt >= VIEW_RESUME_MS) flush();
      else renewIn(sentAt + VIEW_RENEW_MS - Date.now());
    };
    document.addEventListener("visibilitychange", onVisibility);
    channel.current = { up, send, report: flush };

    const info = () => {
      const { doc: current, index: value } = live.current;
      const known = value as DocumentIndex;
      return {
        path: current.path,
        kind: current.kind,
        title: known.title ?? null,
        bytes: known.bytes ?? 0,
        revision: known.version!,
        units: known.units ?? 0,
        outline: known.outline ?? [],
      };
    };
    const snapshot = () => {
      const pointer = live.current.reveal;
      if (pointer) sentReveal.current = pointer.seq;
      return { document: info(), ...(pointer ? { reveal: { targets: pointer.targets, note: pointer.note ?? undefined, seq: pointer.seq } } : {}) };
    };
    const reference = (ref: ViewerReference) => {
      const { doc: current, index: value } = live.current;
      if (!live.current.chat) throw new Error("这里不能引用");
      if (ref.path !== current.path) throw new Error("引用的文档已切换");
      return docRefOf(current, (value as DocumentIndex).version!, ref);
    };
    const label = (ref: ViewerReference) => `${live.current.doc.filename} · ${ref.at}`;

    const call = async (name: string, args: Record<string, unknown>): Promise<{ value: unknown; transfer?: Transferable[] }> => {
      const current = live.current.doc;
      switch (name) {
        case "load_document": {
          if (args.path !== current.path) throw Object.assign(new Error("该文档不在当前标签"), { code: "not_found" });
          // The viewer is up and draws its own placeholder from here on.
          setFramed(version);
          if (bytes.size) throw new Error("文件下载正在处理中");
          const token = crypto.randomUUID();
          const download = fetch(contentUrl(current.source), { signal: downloads.signal }).then(
            (r) => {
              if (r.ok) return r.arrayBuffer();
              unreadable = sourceError(r.status);
              return Promise.reject(Object.assign(new Error(unreadable), { code: r.status === 404 ? "not_found" : undefined }));
            },
            (error) => {
              if (!downloads.signal.aborted) unreadable = sourceError(null);
              throw error;
            },
          );
          download.catch(() => undefined); // a closed view never asks for it; its abort is not an error
          bytes.set(token, download);
          const known = live.current.index as DocumentIndex;
          return { value: { info: info(), file: { token, chunks: 1, bytes: known.bytes ?? 0 }, blocks: known.blocks } };
        }
        case "document_blocks":
          return { value: { blocks: await loadBlocks() } };
        case "open_document":
          if (typeof args.path === "string" && args.path !== current.path) live.current.onOpen?.(args.path);
          return { value: snapshot() };
        case "get_viewer":
          return { value: snapshot() };
        case "check_document":
          return { value: { revision: (live.current.index as DocumentIndex).version } };
        case "list_documents":
          return { value: { documents: live.current.docs.map((d) => ({ path: d.path, kind: d.kind, bytes: 0 })) } };
        case "report_view":
          putView(args);
          return { value: {} };
        case "release_view":
          clearTimeout(debounce);
          clearTimeout(renewal);
          report = null;
          again = held = false;
          releaseView();
          return { value: {} };
        default:
          // Files come into a conversation through the composer's upload, which scans and stores them.
          throw Object.assign(new Error("请在输入框中添加文件"), { code: "invalid_path" });
      }
    };

    const onMessage = async (event: MessageEvent) => {
      if (closed || event.source !== element.contentWindow || !isChannel(event.data)) return;
      const message = event.data as FrameMessage;
      // Screenshots are base64 PNGs, unlike control messages. Keep their transport bounded while
      // allowing the client-call handler to shrink images exceeding its 2 MiB result budget.
      const limit = message.t === "page-tool-result" ? MAX_CAPTURE_MESSAGE_CHARS : MAX_MESSAGE_CHARS;
      if (JSON.stringify(message).length > limit) return;
      if (message.t === "boot") {
        try {
          const [css, js] = await Promise.all([asset("panel.css", false), asset(`viewer-${live.current.doc.kind}.js`, false)]);
          send({ t: "bundle", nonce, css: css as string, js: js as string });
        } catch {
          // no viewer to show: the paged preview instead of an empty frame
          setStopped({ version });
        }
        return;
      }
      // Page errors reach here before the bundle is up, and as warnings after; only the former stop the viewer.
      if (message.t === "error") {
        console.warn("office viewer:", message.message);
        if (!up) setStopped({ version });
        return;
      }
      if (!("nonce" in message) || message.nonce !== nonce) return;
      if (message.t === "tools") {
        setTools({ version: version!, tools: message.tools });
        return;
      }
      if (message.t === "failed") {
        // Only a failure of the version this frame shows ends it; anything else is a late message from before.
        if (message.version !== version) return;
        console.warn("office viewer:", message.stage, message.message);
        const error = unreadable ?? (message.code === "password" ? "文档已加密，无法预览" : undefined);
        setStopped((prior) => (prior?.version === version ? prior : { version, error }));
        return;
      }
      const reply = async (id: number, work: () => Promise<{ value: unknown; transfer?: Transferable[] }>) => {
        try {
          const { value, transfer } = await work();
          send({ t: "reply", id, ok: true, value }, transfer);
        } catch (error) {
          const failure = error as Error & { code?: string };
          send({ t: "reply", id, ok: false, error: { message: failure.message || "失败", code: failure.code } });
        }
      };
      switch (message.t) {
        case "ready":
          up = true;
          channel.current = { up, send, report: flush };
          send({
            t: "context",
            context: { theme: live.current.dark ? "dark" : "light", locale: "zh-CN", chat: live.current.chat },
            recent: live.current.docs.map((d) => ({ path: d.path, kind: d.kind })),
            open: live.current.doc.path,
          });
          if (live.current.reveal) send({ t: "snapshot", snapshot: snapshot() });
          return;
        case "call":
          return reply(message.id, () => call(message.name, message.args));
        case "asset":
          return reply(message.id, async () => {
            const value = await asset(message.name, message.binary);
            // the cache keeps its copy; the frame gets its own
            return { value: typeof value === "string" ? value : value.slice(0) };
          });
        case "bytes":
          return reply(message.id, async () => {
            const pending = bytes.get(message.token);
            if (!pending) throw new Error("文件已失效，请重新打开");
            bytes.delete(message.token);
            const buffer = await pending;
            const expected = (live.current.index as DocumentIndex).bytes;
            if (expected && buffer.byteLength !== expected) {
              unreadable = sourceError(409);
              throw Object.assign(new Error(unreadable), { code: "document_changed" });
            }
            return { value: buffer, transfer: [buffer] };
          });
        case "reference":
          return reply(message.id, async () => {
            const ref = reference(message.reference);
            live.current.onReference!(ref, label(message.reference));
            return { value: {} };
          });
        case "ask":
          return reply(message.id, async () => {
            const ref = reference(message.reference);
            await live.current.onAsk!(message.question, ref, label(message.reference));
            return { value: {} };
          });
        case "link":
          if (/^https?:\/\//i.test(message.url)) window.open(message.url, "_blank", "noopener,noreferrer");
          return;
        case "page-tool-result": {
          const waiter = captures.get(message.id);
          if (!waiter) return;
          captures.delete(message.id);
          waiter.dispose();
          const result = message.value as { content?: { type: string; data?: string; text?: string }[] } | undefined;
          const image = result?.content?.find((part) => part.type === "image")?.data;
          if (!message.ok || !image) return waiter.reject(new Error(message.error || "截图失败"));
          const size = /\((\d+)×(\d+) px\)/.exec(result?.content?.find((part) => part.type === "text")?.text ?? "");
          const at = /#([^\s]+) \(/.exec(result?.content?.find((part) => part.type === "text")?.text ?? "")?.[1] ?? "";
          return waiter.resolve({ data: image, width: Number(size?.[1] ?? 0), height: Number(size?.[2] ?? 0), at, version: (live.current.index as DocumentIndex).version! });
        }
      }
    };
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      document.removeEventListener("visibilitychange", onVisibility);
      send({ t: "dispose" });
      closed = true;
      downloads.abort();
      structuralBlocks = null;
      clearTimeout(debounce);
      clearTimeout(renewal);
      inflight?.abort();
      releaseView();
      for (const waiter of captures.values()) {
        waiter.dispose();
        waiter.reject(new Error("查看器已关闭"));
      }
      captures.clear();
      bytes.clear();
      channel.current = null;
    };
  }, [ready, contentVersion]);

  // A view reported before the conversation existed, or to another one, is reported where the pane now belongs.
  useEffect(() => {
    channel.current?.report();
  }, [conversationId]);

  // Theme follows the Portal; the frame keeps its document.
  useEffect(() => {
    channel.current?.send({
      t: "context",
      context: { theme: dark ? "dark" : "light", locale: "zh-CN", chat: live.current.chat },
      recent: live.current.docs.map((d) => ({ path: d.path, kind: d.kind })),
    });
  }, [dark]);

  // A new reveal from the model goes to the running viewer; the first one rides on the ready handshake.
  useEffect(() => {
    if (!reveal || reveal.seq <= sentReveal.current || !channel.current?.up || !ready) return;
    sentReveal.current = reveal.seq;
    const known = index as DocumentIndex;
    channel.current.send({
      t: "snapshot",
      snapshot: {
        document: { path: doc.path, kind: doc.kind, title: known.title ?? null, bytes: known.bytes ?? 0, revision: known.version!, units: known.units ?? 0, outline: known.outline ?? [] },
        reveal: { targets: reveal.targets, note: reveal.note ?? undefined, seq: reveal.seq },
      },
    });
  }, [reveal, ready, index, doc]);

  // The frame takes focus first: find puts the caret in its own field, which a frame without focus cannot do.
  const command = (name: ViewerCommand) => {
    frame.current?.focus();
    channel.current?.send({ t: "command", name });
  };
  const shown = ready && framed === contentVersion && tools?.version === contentVersion ? tools.tools : null;

  if (stop?.error)
    return (
      <div className="office-stage">
        <div className="alert error" role="alert">
          {stop.error}
        </div>
      </div>
    );
  if (index === "fallback") return <>{fallback}</>;
  // One placeholder from the index wait until the viewer takes over, so the wait never blinks between phases.
  return (
    <div className="office-stage">
      {/* allow-scripts only: an opaque origin with no cookies, storage or same-origin access to the Portal */}
      {/* aria-label, not title: a frame's title shows as a tooltip over the whole document */}
      {ready ? <iframe key={contentVersion} ref={frame} className="office-frame" src="/frame/office" sandbox="allow-scripts" aria-label={doc.filename} /> : null}
      {ready && framed === contentVersion ? null : <DocLoading key="loading" shape={SHAPES[doc.kind]} />}
      {shown ? (
        <DocTools>
          {shown.drawer !== null ? (
            <button type="button" className="icon-btn" aria-pressed={shown.drawer} aria-label="页面与目录" title="页面与目录" onClick={() => command("drawer")}>
              <IconPages size={16} />
            </button>
          ) : null}
          {shown.find !== null ? (
            <button type="button" className="icon-btn" aria-pressed={shown.find} aria-label="查找" title="查找（⌘F）" onClick={() => command("find")}>
              <IconSearch size={16} />
            </button>
          ) : null}
        </DocTools>
      ) : null}
    </div>
  );
}
