"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, downloadUrl, errorMessage, FileItem, FilePreview, filePreviewUrl, filesByIds } from "../../lib/api";
import { IconCheck, IconChevronRight, IconDownload, IconHistory, IconMessage, IconShare, IconX } from "../icons";
import { askHref } from "../library/file-menu";
import ShareDialog from "../share-dialog";
import FileTile from "../library/file-tile";
import { HtmlDocView } from "./html-doc";
import { formatBytes, timeAgo } from "../../lib/format";
import Markdown from "../markdown";
import Sheets from "./sheets";
import DocumentPages from "./pages";

const POLL_MS = 1500;

/** Where a viewed file's preview comes from: the user's own library, or a share someone opened to them. */
export type FileSource = { preview: (fileId: string) => string; asset: (fileId: string, name: string) => string };
const LIBRARY: FileSource = { preview: (id) => `/v1/files/${id}/preview`, asset: filePreviewUrl };
export const FileSourceContext = createContext<FileSource>(LIBRARY);

/** The preview of one file: what the list already knows when it is ready, else asked for (which starts a render) and polled. */
function usePreview(file: FileItem): FilePreview | null {
  const known = file.preview?.status === "ready" ? file.preview : null;
  const canPreview = file.status === "ready" || (file.status === "pending" && file.meta?.scan?.verdict === "clean");
  const [preview, setPreview] = useState<FilePreview | null>(known);
  const source = useContext(FileSourceContext);
  useEffect(() => {
    setPreview(known);
    if (known || !canPreview) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout>;
    const ask = () =>
      api<FilePreview>(source.preview(file.id))
        .then((p) => {
          if (stale) return;
          setPreview(p);
          if (p.status === "pending") timer = setTimeout(ask, POLL_MS);
        })
        .catch((err) => !stale && setPreview({ status: "failed", reason: errorMessage(err) }));
    ask();
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, file.status, canPreview]);
  return preview;
}

function PageScroll({ file, preview, page }: { file: FileItem; preview: FilePreview; page?: number }) {
  const source = useContext(FileSourceContext);
  const pages = preview.pages || [];
  const total = preview.page_count || pages.length;
  return <DocumentPages pages={pages} src={(name) => source.asset(file.id, name)} documentKey={file.id} total={total} initialPage={page}>
    {preview.status === "ready" && total > pages.length ? <a className="viewer-more" href={downloadUrl(file.download_url)}>
      <IconDownload size={15} /> 下载查看全部 {total} 页
    </a> : null}
  </DocumentPages>;
}

function TextView({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const source = useContext(FileSourceContext);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!preview.text) return;
    let stale = false;
    fetch(source.asset(file.id, preview.text))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => !stale && setText(t))
      .catch((e) => !stale && setError(String(e?.message || e)));
    return () => {
      stale = true;
    };
  }, [file.id, preview.text]);
  if (error) return <Unavailable file={file} reason={error} />;
  if (text === null) return <Waiting />;
  return (
    <div className="viewer-paper">
      {preview.format === "markdown" ? (
        <div className="md">
          <Markdown text={text} />
        </div>
      ) : preview.format === "code" ? (
        <pre className="viewer-code">
          {text.split("\n").map((line, i) => (
            <span key={i}>{line + "\n"}</span>
          ))}
        </pre>
      ) : (
        <pre className="viewer-plain">{text}</pre>
      )}
      {preview.truncated ? (
        <a className="viewer-more" href={downloadUrl(file.download_url)}>
          <IconDownload size={15} /> 下载查看全文
        </a>
      ) : null}
    </div>
  );
}

/** Skills deliver self-contained HTML decks ("double-click to page through"): render them, with the
 * source one tab away. The frame is origin-less (no allow-same-origin), so the deck's page-turn
 * script runs but nothing in it can reach this app's cookies or storage. */
function HtmlView({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const [mode, setMode] = useState<"render" | "code">("render");
  return (
    <div className="viewer-html">
      <div className="viewer-modes" role="tablist" aria-label="预览方式">
        <button type="button" role="tab" aria-selected={mode === "render"} className={`viewer-mode ${mode === "render" ? "on" : ""}`} onClick={() => setMode("render")}>
          渲染
        </button>
        <button type="button" role="tab" aria-selected={mode === "code"} className={`viewer-mode ${mode === "code" ? "on" : ""}`} onClick={() => setMode("code")}>
          源码
        </button>
      </div>
      {mode === "render" ? (
        <HtmlDocView url={downloadUrl(file.download_url)} title={file.filename} renderError={(reason) => <Unavailable file={file} reason={reason} />} />
      ) : (
        <TextView file={file} preview={preview} />
      )}
    </div>
  );
}

function ImageView({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const source = useContext(FileSourceContext);
  const [zoomed, setZoomed] = useState(false);
  return (
    <div className={`viewer-image ${zoomed ? "zoomed" : ""}`} onClick={() => setZoomed(!zoomed)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={source.asset(file.id, preview.image || "original")} alt={file.filename} decoding="async" />
    </div>
  );
}

function Waiting() {
  return (
    <div className="viewer-state">
      <span className="viewer-spinner" aria-label="正在生成预览" />
    </div>
  );
}

function Unavailable({ file, reason }: { file: FileItem; reason?: string }) {
  return (
    <div className="viewer-state">
      <FileTile file={file} size={72} />
      <strong>{file.status === "pending" ? "正在扫描" : file.status === "rejected" ? "文件已被拒绝" : "无法预览"}</strong>
      {reason ? <span className="hint">{reason}</span> : null}
      {file.status === "ready" ? (
        <a className="btn secondary sm" href={downloadUrl(file.download_url)}>
          <IconDownload size={15} /> 下载
        </a>
      ) : null}
    </div>
  );
}

export function FilePreviewBody({ file, page }: { file: FileItem; page?: number }) {
  const source = useContext(FileSourceContext);
  const preview = usePreview(file);
  const canPreview = file.status === "ready" || (file.status === "pending" && file.meta?.scan?.verdict === "clean");
  if (!canPreview) return <Unavailable file={file} />;
  if (!preview || (preview.status === "pending" && !(preview.kind === "pages" && preview.pages?.length))) return <Waiting />;
  if (preview.status === "pending" && preview.kind === "pages") return <PageScroll file={file} preview={preview} page={page} />;
  if (preview.status !== "ready") return <Unavailable file={file} reason={preview.status === "failed" ? preview.reason : undefined} />;
  switch (preview.kind) {
    case "pages":
      return <PageScroll file={file} preview={preview} page={page} />;
    case "sheets":
      return (
        <div className="viewer-sheet">
          <Sheets src={preview.data ? source.asset(file.id, preview.data) : null} />
        </div>
      );
    case "text":
      return (file.content_type || "").split(";")[0] === "text/html" || /\.(html?|xhtml)$/i.test(file.filename) ? (
        <HtmlView file={file} preview={preview} />
      ) : (
        <TextView file={file} preview={preview} />
      );
    case "image":
      return <ImageView file={file} preview={preview} />;
    default:
      return <Unavailable file={file} />;
  }
}

/** Every version of the file; picking one shows it in place. Loaded only when opened. */
function Versions({ file, shown, onPick }: { file: FileItem; shown: FileItem; onPick: (version: FileItem) => void }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<FileItem[] | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    let stale = false;
    api<FileItem[]>(`/v1/files/${file.id}/versions`)
      .then((v) => !stale && setList(v))
      .catch(() => !stale && setList([]));
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => {
      stale = true;
      document.removeEventListener("mousedown", close);
    };
  }, [open, file.id]);
  return (
    <div className="viewer-versions" ref={box}>
      <button className={`viewer-chip ${shown.id !== file.id ? "on" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open} aria-label="版本历史">
        <IconHistory size={15} /> v{shown.version}
      </button>
      {open ? (
        <div className="viewer-menu" role="menu">
          {!list ? <div className="viewer-menu-wait" /> : null}
          {list?.map((v) => (
            <button
              key={v.id}
              role="menuitemradio"
              aria-checked={v.id === shown.id}
              className="viewer-menu-item"
              disabled={v.purged}
              onClick={() => {
                setOpen(false);
                onPick(v);
              }}
            >
              <span className="v">v{v.version}</span>
              <span className="grow">{v.purged ? "已清理" : `${timeAgo(v.created_at)} · ${formatBytes(v.size)}`}</span>
              {v.id === shown.id ? <IconCheck size={14} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Opens a library file in place. ← → step through the list it was opened from; Esc closes. */
export default function FileViewer({
  files,
  file,
  onOpen,
  onClose,
  onChanged,
  onRestore,
  chatHref,
  page,
}: {
  files: FileItem[];
  file: FileItem;
  page?: number; // open a document at this page (a citation points there)
  onOpen: (file: FileItem) => void;
  onClose: () => void;
  onChanged?: (head: FileItem) => void; // an old version was restored: the list has a new head
  onRestore?: (file: FileItem) => Promise<void>; // the file is in the trash: read it, then bring it back
  chatHref?: (file: FileItem) => string;
}) {
  const [version, setVersion] = useState<FileItem | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState("");
  const [sharing, setSharing] = useState(false);
  const own = useContext(FileSourceContext) === LIBRARY; // a file shared to this user is theirs to read, not to pass on
  useEffect(() => {
    setVersion(null);
    setRestoreError("");
  }, [file.id]);
  const shown = version || file;
  const trashed = !!file.deleted_at; // readable, not to be worked with until it is restored

  async function restoreFile() {
    setRestoring(true);
    setRestoreError("");
    try {
      await onRestore!(file);
    } catch (err: any) {
      setRestoreError(`恢复失败：${errorMessage(err)}`);
      setRestoring(false);
    }
  }

  async function restoreVersion(old: FileItem) {
    setRestoring(true);
    setRestoreError("");
    try {
      const head = await api<FileItem>(`/v1/files/${old.id}/restore-version`, { method: "POST" });
      setVersion(null);
      onChanged ? onChanged(head) : onOpen(head);
    } catch (err: any) {
      setRestoreError(`恢复失败：${errorMessage(err)}`);
    } finally {
      setRestoring(false);
    }
  }

  const index = files.findIndex((f) => f.id === file.id);
  const prev = index > 0 ? files[index - 1] : null;
  const next = index >= 0 && index < files.length - 1 ? files[index + 1] : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.("input, textarea, select")) return;
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && prev) onOpen(prev);
      else if (e.key === "ArrowRight" && next) onOpen(next);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [prev, next, onOpen, onClose]);

  useEffect(() => {
    const body = document.body.style;
    const before = body.overflow;
    body.overflow = "hidden";
    return () => {
      body.overflow = before;
    };
  }, []);

  return createPortal(
    <div className="viewer" role="dialog" aria-modal="true" aria-label={file.filename}>
      <header className="viewer-bar">
        <FileTile file={shown} size={28} />
        <span className="viewer-name" title={shown.filename}>
          {shown.filename}
        </span>
        {(file.versions || 0) > 1 && !trashed ? <Versions file={file} shown={shown} onPick={(v) => setVersion(v.id === file.id ? null : v)} /> : null}
        {restoreError ? <span className="viewer-error" role="alert">{restoreError}</span> : null}
        {version ? (
          <button className="viewer-chip primary" disabled={restoring} onClick={() => restoreVersion(version)}>
            恢复此版本
          </button>
        ) : null}
        {trashed && onRestore ? (
          <button className="viewer-chip primary" disabled={restoring} onClick={restoreFile}>
            恢复
          </button>
        ) : null}
        {shown.status === "ready" ? (
          <>
            {chatHref && !version && !trashed ? (
              <a className="icon-btn" href={chatHref(file)} title="围绕它对话" aria-label={`围绕 ${file.filename} 对话`}>
                <IconMessage size={18} />
              </a>
            ) : null}
            {own && !version && !trashed ? (
              <button className="icon-btn" onClick={() => setSharing(true)} title="分享" aria-label={`分享 ${file.filename}`}>
                <IconShare size={18} />
              </button>
            ) : null}
            <a className="icon-btn" href={downloadUrl(shown.download_url)} title="下载" aria-label={`下载 ${shown.filename}`}>
              <IconDownload size={18} />
            </a>
          </>
        ) : null}
        <button className="icon-btn" onClick={onClose} aria-label="关闭预览">
          <IconX size={18} />
        </button>
      </header>
      {sharing ? <ShareDialog target={{ kind: "file", fileId: file.id }} title={file.filename} onClose={() => setSharing(false)} /> : null}
      <div className="viewer-stage" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <FilePreviewBody key={shown.id} file={shown} page={shown.id === file.id ? page : undefined} />
        {prev ? (
          <button className="viewer-nav prev" onClick={() => onOpen(prev)} aria-label="上一个文件">
            <IconChevronRight size={22} />
          </button>
        ) : null}
        {next ? (
          <button className="viewer-nav next" onClick={() => onOpen(next)} aria-label="下一个文件">
            <IconChevronRight size={22} />
          </button>
        ) : null}
      </div>
    </div>,
    document.body
  );
}

function GoneViewer({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return createPortal(
    <div className="viewer" role="dialog" aria-modal="true" aria-label={message}>
      <header className="viewer-bar">
        <span className="viewer-name" />
        <button className="icon-btn" onClick={onClose} aria-label="关闭预览">
          <IconX size={18} />
        </button>
      </header>
      <div className="viewer-stage" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="viewer-state">
          <strong>{message}</strong>
        </div>
      </div>
    </div>,
    document.body
  );
}

/** Opens the viewer from places that hold only file ids (a turn's attachments, its artifacts). */
export function useFileViewer(): { open: (ids: string[], id: string, page?: number) => void; viewer: React.ReactNode } {
  const [state, setState] = useState<{ files: FileItem[]; file: FileItem; page?: number } | { gone: string } | null>(null);
  const asked = useRef(0);
  const open = useCallback((ids: string[], id: string, page?: number) => {
    const ticket = ++asked.current; // a later click wins over a slower earlier lookup
    filesByIds(ids)
      .then((files) => {
        if (ticket !== asked.current) return;
        const file = files.find((f) => f.id === id);
        // the lookup leaves out trashed and purged files: say so instead of ignoring the click
        setState(file ? { files, file, page } : { gone: "文件已删除" });
      })
      .catch((err) => ticket === asked.current && setState({ gone: errorMessage(err) }));
  }, []);
  const close = useCallback(() => setState(null), []);
  const viewer = !state ? null : "gone" in state ? (
    <GoneViewer message={state.gone} onClose={close} />
  ) : (
    <FileViewer
      files={state.files}
      file={state.file}
      page={state.page}
      onOpen={(file) => setState((s) => (s && !("gone" in s) ? { ...s, file, page: undefined } : s))}
      onClose={close}
      chatHref={(f) => askHref(f.id, f.project_id)}
    />
  );
  return { open, viewer };
}
