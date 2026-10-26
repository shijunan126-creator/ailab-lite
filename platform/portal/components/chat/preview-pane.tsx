"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { downloadUrl, Preview, previewUrl, readerRenderUrl } from "../../lib/api";
import { IconDownload, IconPanelRight, IconX } from "../icons";
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

/** A web tab: the original page through the render proxy, loaded on first show and kept mounted after. */
function ReaderView({ url, title, active }: { url: string; title: string; active: boolean }) {
  const [opened, setOpened] = useState(false);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  return opened ? <iframe className="reader-frame" src={readerRenderUrl(url)} sandbox="allow-popups" referrerPolicy="no-referrer" title={title || url} /> : null;
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
  const historyGeneration = useRef(0);
  useLayoutEffect(() => {
    const generation = ++historyGeneration.current;
    const opener = document.activeElement as HTMLElement | null;
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    const marker = `preview-${generation}-${Date.now()}`;
    const pop = () => close.current();
    // Install before paint: once the pane is interactive, browser Back must close it.
    // Strict Mode replays layout effects; reuse that entry instead of traversing history.
    if (mobile) {
      const state = { ...window.history.state, ailabPreview: marker };
      if (window.history.state?.ailabPreview) window.history.replaceState(state, "");
      else window.history.pushState(state, "");
      window.addEventListener("popstate", pop);
    }
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
      window.removeEventListener("keydown", key);
      window.removeEventListener("popstate", pop);
      queueMicrotask(() => {
        if (historyGeneration.current !== generation) return;
        if (mobile && window.history.state?.ailabPreview === marker) window.history.back();
      });
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const tabs = pane.current?.querySelector<HTMLElement>(".preview-tabs");
    const active = tabs?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!tabs || !active) return;
    active.scrollLeft = 0;
    const rect = active.closest(".pane-tab")!.getBoundingClientRect(), box = tabs.getBoundingClientRect();
    // Reveal the start even when the selected tab is wider than the available strip.
    if (rect.left < box.left || rect.right > box.right) tabs.scrollLeft += rect.left - box.left;
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
        <button type="button" className="icon-btn" aria-label="收起预览" onClick={onClose}><IconPanelRight size={16} /></button>
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
        ) : showReader ? (
          <a className="reader-open" href={reader.url} target="_blank" rel="noreferrer noopener" aria-label="在新标签打开">↗</a>
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
