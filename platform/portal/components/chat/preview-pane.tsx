"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { downloadUrl, errorMessage, Preview, previewUrl, reader as fetchReader, ReaderPage } from "../../lib/api";
import { IconDownload, IconX } from "../icons";
import Sheets from "../preview/sheets";
import DocumentPages from "../preview/pages";
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

function ReaderView({ url }: { url: string }) {
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
        <span className="viewer-spinner" aria-label="正在抓取网页" />
      </div>
    );
  return (
    <article className="reader">
      <h2 className="reader-title">{page.title}</h2>
      <a className="reader-url" href={page.url} target="_blank" rel="noreferrer noopener">{page.url}</a>
      <div className="reader-text">
        {page.text.split("\n").map((line, i) => (line.trim() ? <p key={i}>{line}</p> : null))}
      </div>
      {page.truncated ? (
        <div className="hint">内容较长，已截断；<a href={page.url} target="_blank" rel="noreferrer noopener">打开原文</a></div>
      ) : null}
    </article>
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
  reader,
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
  reader: PaneReader | null;
  readerOn: boolean;
  onReaderTab: () => void;
  onCloseReader: () => void;
  onRead: (source: Source) => void;
  onClose: () => void;
  live: boolean;
  downloads: Record<string, string>;
}) {
  const pane = useRef<HTMLElement | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    const marker = `preview-${Date.now()}`;
    if (mobile) window.history.pushState({ ...window.history.state, ailabPreview: marker }, "");
    const pop = () => close.current();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); }
      if (mobile && e.key === "Tab") {
        const controls = pane.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], select, [tabindex="0"]');
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    if (mobile) window.addEventListener("popstate", pop);
    pane.current?.querySelector<HTMLButtonElement>(".pane-tab.on button")?.focus();
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("popstate", pop);
      if (mobile && window.history.state?.ailabPreview === marker) window.history.back();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const current = useMemo(() => previews.find((p) => p.filename === selected) || previews[previews.length - 1], [previews, selected]);
  const showReader = !!reader && readerOn;
  const showSources = !!sources && !showReader && (sourcesOn || !current);
  if (!current && !sources && !reader) return null;
  const tab = (key: string, label: string, on: boolean, select: () => void, close: () => void) => (
    <div key={key} className={`pane-tab ${on ? "on" : ""}`}>
      <button type="button" className="pane-tab-name" onClick={select} title={label}>{label}</button>
      <button type="button" className="pane-tab-x" onClick={close} aria-label={`关闭 ${label}`}><IconX size={13} /></button>
    </div>
  );
  return (
    <aside ref={pane} className="preview-pane" aria-label="侧栏">
      <div className="preview-head">
        <div className="preview-tabs" role="tablist">
          {previews.map((p) => tab(p.filename, p.filename, !showSources && p.filename === current?.filename, () => onSelect(p.filename), () => onCloseFile(p.filename)))}
          {sources ? tab("sources", "来源", showSources, onSourcesTab, onCloseSources) : null}
          {reader ? tab(`reader-${reader.url}`, "网页", showReader, onReaderTab, onCloseReader) : null}
        </div>
        <span className="spacer" />
        {!showSources && current ? (
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
      {showReader ? (
        <div className="preview-body reader-body" key={`reader-${reader!.url}`}>
          <ReaderView url={reader!.url} />
        </div>
      ) : showSources ? (
        <div className="preview-body sources-body" key={`sources-${sources!.runId}`}>
          <SourcesList sources={sources!.sources} answer={sources!.answer} onRead={onRead} />
        </div>
      ) : current ? (
        <div className="preview-body" key={`${current.filename}:${current.file_id || ""}`}>
          {current.kind === "pages" ? <Pages preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : current.kind === "html" && current.file_id ? <HtmlPane preview={current} /> : null}
        </div>
      ) : null}
    </aside>
  );
}
