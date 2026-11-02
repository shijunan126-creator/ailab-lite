"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, downloadUrl, type DocRef, type DocumentIndex, type OfficeKind } from "../../lib/api";
import { registerView, type OfficeCapture } from "../../lib/office-views";
import { CHANNEL, isChannel, MAX_CAPTURE_MESSAGE_CHARS, MAX_MESSAGE_CHARS, type FrameMessage, type HostMessage, type ViewerReference } from "../../office/protocol";

/** A document the side pane can open natively: a library file, or a file still in a Work run's workspace. */
export type OfficeDoc = {
  /** The viewer's key for it ("库/报告.docx"): shown in the viewer and used in its anchors. */
  path: string;
  filename: string;
  kind: OfficeKind;
  source: { fileId: string } | { runId: string; path: string };
};

export type OfficeReveal = { seq: number; targets: { at: string; quote?: string }[]; note?: string | null };

type Outgoing = HostMessage extends infer T ? (T extends unknown ? Omit<T, "ch"> : never) : never;

const POLL_MS = 1500;
/** Above this the browser would hold several copies of a large file; the paged preview shows it instead. */
const MAX_NATIVE_BYTES = 64 * 1024 * 1024;
const EXTENSIONS: Record<string, OfficeKind> = { docx: "docx", xlsx: "xlsx", pptx: "pptx", pdf: "pdf" };

export function officeKind(filename: string): OfficeKind | null {
  return EXTENSIONS[filename.toLowerCase().match(/\.([a-z]+)$/)?.[1] ?? ""] ?? null;
}

const indexUrl = (source: OfficeDoc["source"]) =>
  "fileId" in source
    ? `/v1/files/${source.fileId}/document`
    : `/v1/runs/${source.runId}/workspace/document?path=${encodeURIComponent(source.path)}`;
const contentUrl = (source: OfficeDoc["source"]) =>
  downloadUrl("fileId" in source ? `/v1/files/${source.fileId}/content` : `/v1/runs/${source.runId}/workspace/content?path=${encodeURIComponent(source.path)}`);

/** The reference the API will check: the document's identity plus the exact version the user looked at. */
export function docRefOf(doc: OfficeDoc, version: string, ref: { at: string; text: string }): DocRef {
  const quote = ref.text.slice(0, 4000);
  return "fileId" in doc.source
    ? { file_id: doc.source.fileId, version, at: ref.at, quote }
    : { run_id: doc.source.runId, path: doc.source.path, version, at: ref.at, quote };
}

// Viewer files are fetched once per page: the version path makes them immutable.
let assetVersion: Promise<string> | undefined;
const assetCache = new Map<string, Promise<string | ArrayBuffer>>();
function asset(name: string, binary: boolean): Promise<string | ArrayBuffer> {
  const key = `${binary ? "b" : "t"}:${name}`;
  let cached = assetCache.get(key);
  if (!cached) {
    cached = (assetVersion ??= fetch("/office-viewer/current/manifest.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`查看器不可用 ${r.status}`))))
      .then((m: { version: string }) => m.version))
      .then((version) => fetch(`/office-viewer/${version}/${name.split("/").map(encodeURIComponent).join("/")}`))
      .then((r) => (r.ok ? (binary ? r.arrayBuffer() : r.text()) : Promise.reject(new Error(`${name} ${r.status}`))));
    cached.catch(() => {
      assetCache.delete(key);
      assetVersion = undefined;
    });
    assetCache.set(key, cached);
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
function useDocumentIndex(doc: OfficeDoc): DocumentIndex | "fallback" | null {
  const [index, setIndex] = useState<DocumentIndex | "fallback" | null>(null);
  const key = JSON.stringify(doc.source);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setIndex(null);
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
  }, [key]);
  return index;
}

type Props = {
  doc: OfficeDoc;
  /** Every document open in the pane, for the viewer's switch menu. */
  docs: OfficeDoc[];
  conversationId?: string | null;
  reveal?: OfficeReveal | null;
  onOpen: (path: string) => void;
  /** Add the selection to the composer as a quoted reference. */
  onReference: (ref: DocRef, label: string) => void;
  /** Send a question about the selection right away. */
  onAsk: (question: string, ref: DocRef, label: string) => Promise<void>;
  /** Shown when the document cannot be viewed natively (too large, unsupported, index failed). */
  fallback: ReactNode;
};

/** The native Word/Excel/PowerPoint/PDF viewer in a sandboxed frame (app/frame/office), driven over postMessage. */
export default function OfficeView({ doc, docs, conversationId, reveal, onOpen, onReference, onAsk, fallback }: Props) {
  const loaded = useDocumentIndex(doc);
  // the viewer failed before it was up (bundle error): show the paged preview instead of an empty frame
  const [broken, setBroken] = useState(false);
  const index = broken ? "fallback" : loaded;
  const dark = useDark();
  const frame = useRef<HTMLIFrameElement>(null);
  const live = useRef({ doc, docs, conversationId, reveal, onOpen, onReference, onAsk, dark, index });
  live.current = { doc, docs, conversationId, reveal, onOpen, onReference, onAsk, dark, index };
  const channel = useRef<{ nonce: string; viewId: string | null; send(message: Outgoing, transfer?: Transferable[]): void } | null>(null);
  const sentReveal = useRef(0);
  const ready = index !== null && index !== "fallback";

  useEffect(() => {
    const element = frame.current;
    if (!ready || !element) return;
    const nonce = crypto.randomUUID();
    const bytes = new Map<string, Promise<ArrayBuffer>>();
    const captures = new Map<number, { resolve(value: OfficeCapture): void; reject(error: Error): void }>();
    let toolSeq = 0;
    let viewId: string | null = null;
    let unregister: (() => void) | undefined;
    let viewTimer: ReturnType<typeof setTimeout> | undefined;
    const viewAbort = new AbortController();
    const send = (message: Outgoing, transfer: Transferable[] = []) =>
      element.contentWindow?.postMessage({ ch: CHANNEL, ...message }, "*", transfer);
    channel.current = { nonce, viewId, send };
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
      if (ref.path !== current.path) throw new Error("引用的文档已切换");
      return docRefOf(current, (value as DocumentIndex).version!, ref);
    };
    const label = (ref: ViewerReference) => `${live.current.doc.filename} · ${ref.at}`;
    const putView = (args: Record<string, unknown>) => {
      const id = live.current.conversationId;
      if (!id || !viewId) return;
      clearTimeout(viewTimer);
      viewTimer = setTimeout(() => {
        const selection = args.selection as { at: string; text: string } | null;
        const target = "fileId" in live.current.doc.source ? { file_id: live.current.doc.source.fileId } : { run_id: live.current.doc.source.runId, path: live.current.doc.source.path };
        void api(`/v1/conversations/${id}/view`, {
          method: "PUT",
          body: JSON.stringify({
            view_id: viewId,
            ...target,
            version: (live.current.index as DocumentIndex).version,
            at: typeof args.at === "string" ? args.at : null,
            selection: selection ? { at: selection.at, text: selection.text.slice(0, 4000) } : null,
          }),
          signal: viewAbort.signal,
        }).catch(() => undefined);
      }, 600);
    };
    const releaseView = () => {
      const id = live.current.conversationId;
      if (id && viewId) void fetch(downloadUrl(`/v1/conversations/${id}/view?view_id=${encodeURIComponent(viewId)}`), { method: "DELETE", keepalive: true }).catch(() => undefined);
    };

    const call = async (name: string, args: Record<string, unknown>): Promise<{ value: unknown; transfer?: Transferable[] }> => {
      const current = live.current.doc;
      switch (name) {
        case "load_document": {
          if (args.path !== current.path) throw Object.assign(new Error("该文档不在当前标签"), { code: "not_found" });
          const token = crypto.randomUUID();
          bytes.set(
            token,
            fetch(contentUrl(current.source)).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(Object.assign(new Error(`${r.status}`), { code: r.status === 404 ? "not_found" : undefined })))),
          );
          const known = live.current.index as DocumentIndex;
          return { value: { info: info(), file: { token, chunks: 1, bytes: known.bytes ?? 0 }, blocks: known.blocks } };
        }
        case "open_document":
          if (typeof args.path === "string" && args.path !== current.path) live.current.onOpen(args.path);
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
          releaseView();
          return { value: {} };
        default:
          // Files come into a conversation through the composer's upload, which scans and stores them.
          throw Object.assign(new Error("请在输入框中添加文件"), { code: "invalid_path" });
      }
    };

    const onMessage = async (event: MessageEvent) => {
      if (event.source !== element.contentWindow || !isChannel(event.data)) return;
      const message = event.data as FrameMessage;
      // Screenshots are base64 PNGs, unlike control messages. Keep their transport bounded while
      // allowing the client-call handler to shrink images exceeding its 2 MiB result budget.
      const limit = message.t === "page-tool-result" ? MAX_CAPTURE_MESSAGE_CHARS : MAX_MESSAGE_CHARS;
      if (JSON.stringify(message).length > limit) return;
      if (message.t === "boot") {
        try {
          const [css, js] = await Promise.all([asset("panel.css", false), asset("viewer.js", false)]);
          send({ t: "bundle", nonce, css: css as string, js: js as string });
        } catch {
          /* the frame keeps its empty state; a reload retries */
        }
        return;
      }
      if (message.t === "error") {
        console.warn("office viewer:", message.message);
        if (!viewId) setBroken(true);
        return;
      }
      if (!("nonce" in message) || message.nonce !== nonce) return;
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
          viewId = message.viewId;
          channel.current = { nonce, viewId, send };
          unregister?.();
          unregister = registerView(viewId, (at, maxWidth) =>
            new Promise<OfficeCapture>((resolve, reject) => {
              const id = ++toolSeq;
              captures.set(id, { resolve, reject });
              send({ t: "page-tool", id, name: "capture_view", args: { ...(at ? { anchor: `${live.current.doc.path}#${at}` } : {}), maxWidth } });
              setTimeout(() => captures.get(id) && (captures.delete(id), reject(new Error("截图超时"))), 15_000);
            }),
          );
          send({
            t: "context",
            context: { theme: live.current.dark ? "dark" : "light", locale: "zh-CN" },
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
            return { value: buffer, transfer: [buffer] };
          });
        case "reference":
          return reply(message.id, async () => {
            live.current.onReference(reference(message.reference), label(message.reference));
            return { value: {} };
          });
        case "ask":
          return reply(message.id, async () => {
            await live.current.onAsk(message.question, reference(message.reference), label(message.reference));
            return { value: {} };
          });
        case "link":
          if (/^https?:\/\//i.test(message.url)) window.open(message.url, "_blank", "noopener,noreferrer");
          return;
        case "page-tool-result": {
          const waiter = captures.get(message.id);
          if (!waiter) return;
          captures.delete(message.id);
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
      clearTimeout(viewTimer);
      viewAbort.abort();
      releaseView();
      unregister?.();
      for (const waiter of captures.values()) waiter.reject(new Error("查看器已关闭"));
      channel.current = null;
    };
  }, [ready]);

  // Theme follows the Portal; the frame keeps its document.
  useEffect(() => {
    channel.current?.send({
      t: "context",
      context: { theme: dark ? "dark" : "light", locale: "zh-CN" },
      recent: live.current.docs.map((d) => ({ path: d.path, kind: d.kind })),
    });
  }, [dark]);

  // A new reveal from the model goes to the running viewer; the first one rides on the ready handshake.
  useEffect(() => {
    if (!reveal || reveal.seq <= sentReveal.current || !channel.current?.viewId || !ready) return;
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

  if (index === "fallback") return <>{fallback}</>;
  if (index === null) return <div className="hint" role="status">正在加载</div>;
  // allow-scripts only: an opaque origin with no cookies, storage or same-origin access to the Portal
  return <iframe ref={frame} className="office-frame" src="/frame/office" sandbox="allow-scripts" title={doc.filename} />;
}
