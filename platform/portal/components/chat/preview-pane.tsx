"use client";

import { createElement, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { downloadUrl, errorMessage, Preview, previewUrl, reader as fetchReader, readerRenderUrl, ReaderBlock, ReaderPage } from "../../lib/api";
import { IconArrowLeft, IconDownload, IconX } from "../icons";
import Sheets from "../preview/sheets";
import DocumentPages from "../preview/pages";
import PdfDocument from "../preview/pdf";
import { HtmlDocView } from "../preview/html-doc";
import type { Source } from "../../lib/work-process";
import { SourcesList } from "./sources";

function Pages({ preview }: { preview: Preview }) {
  const pages = preview.pages || [];
  if (!pages.length || !preview.run_id) return <div className="hint">没有可显示的页面</div>;
  return <DocumentPages pages={pages} src={(name) => previewUrl(preview.run_id!, name)} documentKey={`${preview.run_id}:${preview.filename}`} thumbnails />;
}

export type PaneSources = { runId: string; sources: Source[]; answer: string };
export type PaneReader = { url: string; title: string };

/** A delivered HTML deck, rendered beside the conversation: the newest delivery wins, and a
 * regenerated file (new file id) refetches by itself. */
function HtmlPane({ preview }: { preview: Preview }) {
  return (
    <HtmlDocView
      url={downloadUrl(`/v1/files/${preview.file_id}/content`)}
      title={preview.filename}
      renderError={(reason) => <div className="hint" style={{ margin: 16 }}>无法加载文件：{reason}</div>}
    />
  );
}

/** Word, PowerPoint and PDF arrive as one PDF: PDF.js keeps the text layer and zoom that JPEG pages lose. */
function PdfPane({ preview }: { preview: Preview }) {
  if (!preview.run_id || !preview.pdf) return <div className="hint">没有可显示的文档</div>;
  return <PdfDocument url={previewUrl(preview.run_id, preview.pdf)} title={preview.filename} />;
}

/** The extracted page as blocks: headings, list runs, images and code — the shape the reader API
 * returns, so chrome the extractor dropped never reaches the pane. */
function BlocksView({ blocks }: { blocks: ReaderBlock[] }) {
  const out: ReactNode[] = [];
  let list: { key: string; ordered: boolean; items: ReaderBlock[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((b, i) => <li key={i}>{b.text}</li>);
    out.push(list.ordered ? <ol key={list.key} className="reader-list">{items}</ol> : <ul key={list.key} className="reader-list">{items}</ul>);
    list = null;
  };
  blocks.forEach((b, i) => {
    const key = `b${i}`;
    if (b.type === "li") {
      if (!list || list.ordered !== !!b.ordered) {
        flush();
        list = { key, ordered: !!b.ordered, items: [] };
      }
      list.items.push(b);
      return;
    }
    flush();
    if (b.type === "img") {
      out.push(<img key={key} className="reader-img" src={b.src} alt={b.alt || ""} loading="lazy" referrerPolicy="no-referrer" />);
    } else if (b.type === "pre") {
      out.push(<pre key={key} className="reader-pre">{b.text}</pre>);
    } else if (b.type.startsWith("h")) {
      // the page title is the pane's h2: document headings render two levels down from their own
      const rank = Math.min((Number(b.type.slice(1)) || 1) + 2, 5);
      out.push(createElement(`h${rank}`, { key, className: "reader-h" }, b.text));
    } else {
      out.push(<p key={key}>{b.text}</p>);
    }
  });
  flush();
  return <>{out}</>;
}

/** The 正文 view: the server-extracted article, fetched on first open. */
function ReaderText({ url }: { url: string }) {
  const [page, setPage] = useState<ReaderPage | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setPage(null);
    setError("");
    const ctl = new AbortController();
    fetchReader(url, ctl.signal)
      .then((p) => !ctl.signal.aborted && setPage(p))
      .catch((e) => !ctl.signal.aborted && setError(errorMessage(e)));
    return () => ctl.abort();
  }, [url]);
  if (error)
    return (
      <div className="reader">
        <div className="hint">{error}</div>
        <a className="viewer-more" href={url} target="_blank" rel="noreferrer noopener">在新标签打开原文</a>
      </div>
    );
  if (!page)
    return (
      <div className="viewer-state">
        <span className="viewer-spinner" aria-label="正在提取正文" />
      </div>
    );
  return (
    <article className="reader">
      <h2 className="reader-title">{page.title}</h2>
      <a className="reader-url" href={page.url} target="_blank" rel="noreferrer noopener">{page.url}</a>
      {page.blocks.length ? (
        <div className="reader-text">
          <BlocksView blocks={page.blocks} />
        </div>
      ) : (
        <div className="hint">未能提取正文（页面可能是脚本渲染）；<a href={page.url} target="_blank" rel="noreferrer noopener">打开原文</a></div>
      )}
      {page.truncated ? (
        <div className="hint">内容较长，已截断；<a href={page.url} target="_blank" rel="noreferrer noopener">打开原文</a></div>
      ) : null}
    </article>
  );
}

/** The 网页 view: the original page through the render proxy by default, the extracted article one
 * tab away for pages the proxy shows poorly. Mode resets per URL (the wrapper remounts us). */
function ReaderView({ url, title, active }: { url: string; title: string; active: boolean }) {
  const [opened, setOpened] = useState(false);
  const [textOpened, setTextOpened] = useState(false);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  const [mode, setMode] = useState<"page" | "text">("page");
  return (
    <div className="reader-wrap">
      <div className="reader-modes" role="tablist" aria-label="查看方式">
        <button type="button" role="tab" aria-selected={mode === "page"} className={`viewer-mode ${mode === "page" ? "on" : ""}`} onClick={() => setMode("page")}>
          原版
        </button>
        <button type="button" role="tab" aria-selected={mode === "text"} className={`viewer-mode ${mode === "text" ? "on" : ""}`} onClick={() => { setTextOpened(true); setMode("text"); }}>
          正文
        </button>
        <a className="reader-open" href={url} target="_blank" rel="noreferrer noopener" title="在新标签打开原文">↗</a>
      </div>
      {opened ? <iframe hidden={mode !== "page"} className="reader-frame" src={readerRenderUrl(url)} sandbox="allow-popups" referrerPolicy="no-referrer" title={title || url} /> : null}
      {textOpened ? (
        <div className="reader-scroll" hidden={mode !== "text"}>
          <ReaderText url={url} />
        </div>
      ) : null}
    </div>
  );
}

/** The side pane: one tab per previewed file and one for a turn's sources, each closed on its own. */
export default function PreviewPane({
  previews,
  selected,
  onSelect,
  onCloseFile,
  sources,
  sourcesOn,
  onSourcesTab,
  onCloseSources,
  readers,
  readerUrl,
  readerOn,
  onReaderTab,
  onCloseReader,
  onRead,
  onClose,
  live,
  downloads,
}: {
  previews: Preview[];
  selected: string | null;
  onSelect: (filename: string) => void;
  onCloseFile: (filename: string) => void;
  sources: PaneSources | null;
  sourcesOn: boolean;
  onSourcesTab: () => void;
  onCloseSources: () => void;
  readers: PaneReader[];
  readerUrl: string | null;
  readerOn: boolean;
  onReaderTab: (url: string) => void;
  onCloseReader: (url: string) => void;
  onRead: (source: Source) => void;
  onClose: () => void;
  live: boolean;
  downloads: Record<string, string>;
}) {
  // Keep recent documents mounted for instant switches, bound third-party DOM/frame memory.
  const [recentReaders, setRecentReaders] = useState<string[]>([]);
  useEffect(() => {
    if (readerOn && readerUrl) setRecentReaders((prev) => [...prev.filter((url) => url !== readerUrl), readerUrl].slice(-4));
  }, [readerOn, readerUrl]);
  const pane = useRef<HTMLElement | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    const marker = `preview-${Date.now()}`;
    const pop = () => close.current();
    const push = mobile ? setTimeout(() => {
      window.history.pushState({ ...window.history.state, ailabPreview: marker }, "");
      window.addEventListener("popstate", pop);
    }) : undefined;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); }
      if (mobile && e.key === "Tab") {
        const controls = Array.from(pane.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], select, [tabindex="0"]') || []).filter((el) => el.getClientRects().length > 0);
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    pane.current?.querySelector<HTMLButtonElement>(".pane-tab.on button")?.focus({ preventScroll: true });
    window.addEventListener("keydown", key);
    return () => {
      clearTimeout(push);
      window.removeEventListener("keydown", key);
      window.removeEventListener("popstate", pop);
      if (mobile && window.history.state?.ailabPreview === marker) window.history.back();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const tabs = pane.current?.querySelector<HTMLElement>(".preview-tabs");
    const active = tabs?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!tabs || !active) return;
    const rect = active.getBoundingClientRect(), box = tabs.getBoundingClientRect();
    if (rect.left < box.left) tabs.scrollLeft += rect.left - box.left;
    else if (rect.right > box.right) tabs.scrollLeft += rect.right - box.right;
  }, [readerUrl, readerOn, selected, sourcesOn]);
  const current = useMemo(() => previews.find((p) => p.filename === selected) || previews[previews.length - 1], [previews, selected]);
  const reader = readers.find((r) => r.url === readerUrl);
  const showReader = !!reader && readerOn;
  const showSources = !!sources && !showReader && (sourcesOn || !current);
  if (!current && !sources && !reader) return null;
  const tab = (key: string, label: string, on: boolean, select: () => void, close: () => void) => (
    <div key={key} className={`pane-tab ${on ? "on" : ""}`}>
      <button type="button" role="tab" aria-selected={on} className="pane-tab-name" onClick={select} title={label}>{label}</button>
      <button type="button" className="pane-tab-x" onClick={close} aria-label={`关闭 ${label}`}><IconX size={13} /></button>
    </div>
  );
  return (
    <aside ref={pane} className="preview-pane" aria-label="侧栏">
      <div className="preview-head">
        <button type="button" className="icon-btn" aria-label="返回对话" onClick={onClose}><IconArrowLeft size={16} /></button>
        <div className="preview-tabs" role="tablist">
          {previews.map((p) => tab(p.filename, p.filename, !showSources && !showReader && p.filename === current?.filename, () => onSelect(p.filename), () => onCloseFile(p.filename)))}
          {sources ? tab("sources", "来源", showSources, onSourcesTab, onCloseSources) : null}
          {readers.map((r) => tab(`reader-${r.url}`, r.title || r.url, showReader && r.url === readerUrl, () => onReaderTab(r.url), () => onCloseReader(r.url)))}
        </div>
        <span className="spacer" />
        {!showSources && !showReader && current ? (
          <>
            {live ? <span className="live on">实时</span> : null}
            {downloads[current.filename] ? (
              <a className="btn secondary sm" href={downloads[current.filename]} target="_blank" rel="noreferrer" aria-label={`下载 ${current.filename}`}>
                <IconDownload size={15} />
              </a>
            ) : null}
          </>
        ) : null}
      </div>
      {readers.filter((r) => r.url === readerUrl || recentReaders.includes(r.url)).map((r) => (
        <div className="preview-body reader-body" key={`reader-${r.url}`} hidden={!showReader || r.url !== readerUrl}>
          <ReaderView url={r.url} title={r.title} active={showReader && r.url === readerUrl} />
        </div>
      ))}
      {showReader ? null : showSources ? (
        <div className="preview-body sources-body" key={`sources-${sources!.runId}`}>
          <SourcesList sources={sources!.sources} answer={sources!.answer} onRead={onRead} />
        </div>
      ) : current ? (
        <div className="preview-body" key={`${current.filename}:${current.file_id || ""}`}>
          {current.kind === "pages" ? <Pages preview={current} /> : current.kind === "pdf" ? <PdfPane preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : current.kind === "html" && current.file_id ? <HtmlPane preview={current} /> : null}
        </div>
      ) : null}
    </aside>
  );
}
