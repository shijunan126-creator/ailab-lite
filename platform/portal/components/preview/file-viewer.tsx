"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, downloadUrl, FileItem, FilePreview, filePreviewUrl, filesByIds } from "../../lib/api";
import { IconChevronRight, IconDownload, IconMessage, IconX } from "../icons";
import FileTile from "../library/file-tile";
import Markdown from "../markdown";
import Sheets from "./sheets";

const POLL_MS = 1500;

/** The preview of one file: what the list already knows when it is ready, else asked for (which starts a render) and polled. */
function usePreview(file: FileItem): FilePreview | null {
  const known = file.preview?.status === "ready" ? file.preview : null;
  const [preview, setPreview] = useState<FilePreview | null>(known);
  useEffect(() => {
    setPreview(known);
    if (known || file.status !== "ready") return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout>;
    const ask = () =>
      api<FilePreview>(`/v1/files/${file.id}/preview`)
        .then((p) => {
          if (stale) return;
          setPreview(p);
          if (p.status === "pending") timer = setTimeout(ask, POLL_MS);
        })
        .catch((err) => !stale && setPreview({ status: "failed", reason: String(err?.message || err) }));
    ask();
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, file.status]);
  return preview;
}

function PageScroll({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const pages = preview.pages || [];
  const [current, setCurrent] = useState(1);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    const seen = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setCurrent(Number((e.target as HTMLElement).dataset.page));
      },
      { root, rootMargin: "-45% 0px -45% 0px" } // the page crossing the middle of the view is the current one
    );
    root.querySelectorAll("[data-page]").forEach((el) => seen.observe(el));
    return () => seen.disconnect();
  }, [pages.length]);
  const total = preview.page_count || pages.length;
  return (
    <div className="viewer-pages" ref={box}>
      {pages.map((name, i) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={name} data-page={i + 1} src={filePreviewUrl(file.id, name)} alt={`第 ${i + 1} 页`} loading={i < 2 ? "eager" : "lazy"} decoding="async" />
      ))}
      {total > pages.length ? (
        <a className="viewer-more" href={downloadUrl(file.download_url)}>
          <IconDownload size={15} /> 下载查看全部 {total} 页
        </a>
      ) : null}
      {pages.length > 1 ? (
        <span className="viewer-counter">
          {current} / {total}
        </span>
      ) : null}
    </div>
  );
}

function TextView({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!preview.text) return;
    let stale = false;
    fetch(filePreviewUrl(file.id, preview.text))
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

function ImageView({ file, preview }: { file: FileItem; preview: FilePreview }) {
  const [zoomed, setZoomed] = useState(false);
  return (
    <div className={`viewer-image ${zoomed ? "zoomed" : ""}`} onClick={() => setZoomed(!zoomed)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={filePreviewUrl(file.id, preview.image || "original")} alt={file.filename} decoding="async" />
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

function Body({ file }: { file: FileItem }) {
  const preview = usePreview(file);
  if (file.status !== "ready") return <Unavailable file={file} />;
  if (!preview || preview.status === "pending") return <Waiting />;
  if (preview.status !== "ready") return <Unavailable file={file} reason={preview.status === "failed" ? preview.reason : undefined} />;
  switch (preview.kind) {
    case "pages":
      return <PageScroll file={file} preview={preview} />;
    case "sheets":
      return (
        <div className="viewer-sheet">
          <Sheets src={preview.data ? filePreviewUrl(file.id, preview.data) : null} />
        </div>
      );
    case "text":
      return <TextView file={file} preview={preview} />;
    case "image":
      return <ImageView file={file} preview={preview} />;
    default:
      return <Unavailable file={file} />;
  }
}

/** Opens a library file in place. ← → step through the list it was opened from; Esc closes. */
export default function FileViewer({
  files,
  file,
  onOpen,
  onClose,
  chatHref,
}: {
  files: FileItem[];
  file: FileItem;
  onOpen: (file: FileItem) => void;
  onClose: () => void;
  chatHref?: (file: FileItem) => string;
}) {
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
        <FileTile file={file} size={28} />
        <span className="viewer-name" title={file.filename}>
          {file.filename}
        </span>
        {file.status === "ready" ? (
          <>
            {chatHref ? (
              <a className="icon-btn" href={chatHref(file)} title="围绕它对话" aria-label={`围绕 ${file.filename} 对话`}>
                <IconMessage size={18} />
              </a>
            ) : null}
            <a className="icon-btn" href={downloadUrl(file.download_url)} title="下载" aria-label={`下载 ${file.filename}`}>
              <IconDownload size={18} />
            </a>
          </>
        ) : null}
        <button className="icon-btn" onClick={onClose} aria-label="关闭预览">
          <IconX size={18} />
        </button>
      </header>
      <div className="viewer-stage" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <Body key={file.id} file={file} />
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

/** Opens the viewer from places that hold only file ids (a turn's attachments, its artifacts). */
export function useFileViewer(): { open: (ids: string[], id: string) => void; viewer: React.ReactNode } {
  const [state, setState] = useState<{ files: FileItem[]; file: FileItem } | null>(null);
  const asked = useRef(0);
  const open = useCallback((ids: string[], id: string) => {
    const ticket = ++asked.current; // a later click wins over a slower earlier lookup
    filesByIds(ids)
      .then((files) => {
        const file = files.find((f) => f.id === id);
        if (ticket === asked.current && file) setState({ files, file });
      })
      .catch(() => undefined);
  }, []);
  const close = useCallback(() => setState(null), []);
  const viewer = state ? (
    <FileViewer files={state.files} file={state.file} onOpen={(file) => setState((s) => (s ? { ...s, file } : s))} onClose={close} />
  ) : null;
  return { open, viewer };
}
