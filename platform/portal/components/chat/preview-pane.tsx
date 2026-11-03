"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, downloadUrl, errorMessage, FileItem, Preview, previewUrl, readerRenderUrl, type DocRef } from "../../lib/api";
import OfficeView, { officeKind, type OfficeDoc, type OfficeReveal } from "../preview/office-view";
import { IconDownload, IconExternal, IconPanelRight, IconX } from "../icons";
import { DocToolsSlot } from "../preview/doc-tools";
import { PagedPreview } from "../preview/file-viewer";
import Sheets from "../preview/sheets";
import DocumentPages from "../preview/pages";
import PdfDocument from "../preview/pdf";
import { HtmlDocView } from "../preview/html-doc";
import DocLoading, { loadingShape } from "../preview/doc-loading";
import type { Source } from "../../lib/work-process";
import { SourcesList } from "./sources";

function Pages({ preview }: { preview: Preview }) {
  const pages = preview.pages || [];
  if (!pages.length || !preview.run_id) return <div className="hint">没有可显示的页面</div>;
  return <DocumentPages pages={pages} src={(name) => previewUrl(preview.run_id!, name)} documentKey={`${preview.run_id}:${preview.filename}`} thumbnails />;
}

export type PanePreview = Preview & { libraryFileId?: string };
export const previewTabKey = (preview: PanePreview) => preview.libraryFileId ? `file:${preview.libraryFileId}` : preview.filename;

/** What the native viewer needs from the pane: the documents open in it and where quotes and questions go. */
export type PaneOffice = {
  docs: OfficeDoc[];
  conversationId?: string | null;
  reveals: Record<string, OfficeReveal>;
  onOpen: (path: string) => void;
  onReference: (ref: DocRef, label: string) => void;
  onAsk: (question: string, ref: DocRef, label: string) => Promise<void>;
};

/** The viewer key of a pane document: where it lives, then its name (shown in the viewer, used in its anchors). */
const officePath = (where: "库" | "工作区", filename: string) => `${where}/${filename}`;

/** A pane tab as a native-viewer document, when its format has one. */
function officeDocOf(preview: PanePreview, delivered: Record<string, string>): OfficeDoc | null {
  const kind = officeKind(preview.filename);
  if (!kind) return null;
  if (preview.libraryFileId) return { path: officePath("库", preview.filename), filename: preview.filename, kind, source: { fileId: preview.libraryFileId } };
  const fileId = delivered[preview.filename];
  if (fileId) return { path: officePath("库", preview.filename), filename: preview.filename, kind, source: { fileId } };
  if (!preview.run_id || !preview.path) return null;
  const path = preview.path.replace(/^\/workspace\//, "");
  return { path: officePath("工作区", path), filename: preview.filename, kind, source: { runId: preview.run_id, path } };
}

function NativePane({ doc, revision, office, reveal, fallback }: { doc: OfficeDoc; revision: number; office: PaneOffice; reveal?: OfficeReveal; fallback: React.ReactNode }) {
  return (
    <OfficeView
      doc={doc}
      revision={revision}
      docs={office.docs}
      conversationId={office.conversationId}
      reveal={reveal}
      onOpen={office.onOpen}
      onReference={office.onReference}
      onAsk={office.onAsk}
      fallback={fallback}
    />
  );
}

/** Reuse library rendering without its fullscreen shell; cancel superseded file loads. */
function LibraryPane({ fileId, filename }: { fileId: string; filename: string }) {
  const [file, setFile] = useState<FileItem | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setFile(null);
    setError("");
    api<FileItem>(`/v1/files/${fileId}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setFile(value); })
      .catch((reason) => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [fileId]);
  if (error) return <div className="alert error">{error}</div>;
  return file ? <PagedPreview file={file} /> : <DocLoading shape={loadingShape(filename)} />;
}

const WORKSPACE_POLL_MS = 1500;

/** A Work file the native viewer cannot show: rendered on the server from the run's workspace, asked for only when
 * this fallback is actually on screen, and again when the file changes (`version`). */
function WorkspacePreview({ preview }: { preview: Preview }) {
  const [rendered, setRendered] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const { run_id: runId, path, version } = preview;
  useEffect(() => {
    if (!runId || !path) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setError("");
    setRendered(null);
    const ask = () =>
      api<Preview & { status?: string }>(`/v1/runs/${runId}/workspace/preview?path=${encodeURIComponent(path.replace(/^\/workspace\//, ""))}`, { signal: controller.signal })
        .then((value) => {
          if (controller.signal.aborted) return;
          if (value.status === "pending") timer = setTimeout(ask, WORKSPACE_POLL_MS);
          else if (value.error) setError(value.error);
          else setRendered({ ...value, run_id: value.run_id || runId });
        })
        .catch((reason) => !controller.signal.aborted && setError(errorMessage(reason)));
    ask();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, path, version]);
  if (!runId || !path) return <div className="hint">没有可显示的文档</div>;
  if (error) return <div className="alert error">无法预览：{error}</div>;
  if (!rendered) return <DocLoading shape={loadingShape(preview.filename)} />;
  return rendered.kind === "pdf" ? <PdfPane preview={rendered} /> : rendered.kind === "sheets" ? <Sheets src={rendered.data ? previewUrl(rendered.run_id!, rendered.data) : null} /> : rendered.kind === "pages" ? <Pages preview={rendered} /> : <div className="hint">没有可显示的文档</div>;
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
  return opened ? <iframe className="reader-frame" src={readerRenderUrl(url)} sandbox="allow-popups" referrerPolicy="no-referrer" aria-label={title || url} /> : null;
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
  conversationId,
  reveals = {},
  delivered = {},
  onReference,
  onAsk,
}: {
  previews: PanePreview[];
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
  conversationId?: string | null;
  reveals?: Record<string, OfficeReveal>;
  /** Delivered files by name: a delivered office file opens from the library, its exact version. */
  delivered?: Record<string, string>;
  onReference?: (ref: DocRef, label: string) => void;
  onAsk?: (question: string, ref: DocRef, label: string) => Promise<void>;
}) {
  // Keep recent documents mounted for instant switches, bound third-party DOM/frame memory.
  const [recentReaders, setRecentReaders] = useState<string[]>([]);
  useEffect(() => {
    if (readerOn && readerUrl) setRecentReaders((prev) => [...prev.filter((url) => url !== readerUrl), readerUrl].slice(-4));
  }, [readerOn, readerUrl]);
  const pane = useRef<HTMLElement | null>(null);
  const [toolSlot, setToolSlot] = useState<HTMLElement | null>(null);
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
  const current = useMemo(() => previews.find((p) => previewTabKey(p) === selected) || previews[previews.length - 1], [previews, selected]);
  const officeDocs = useMemo(() => previews.map((p) => ({ key: previewTabKey(p), doc: officeDocOf(p, delivered) })), [previews, delivered]);
  const office: PaneOffice | null = onReference && onAsk ? {
    docs: officeDocs.flatMap((d) => (d.doc ? [d.doc] : [])),
    conversationId,
    reveals,
    onOpen: (path) => {
      const hit = officeDocs.find((d) => d.doc?.path === path);
      if (hit) onSelect(hit.key);
    },
    onReference,
    onAsk,
  } : null;
  const currentDoc = current ? officeDocs.find((d) => d.key === previewTabKey(current))?.doc ?? null : null;
  const revealOf = (preview: PanePreview) => reveals[previewTabKey(preview)];
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
          {previews.map((p) => tab(previewTabKey(p), p.filename, !showSources && !showReader && previewTabKey(p) === (current && previewTabKey(current)), () => onSelect(previewTabKey(p)), () => onCloseFile(previewTabKey(p))))}
          {sources ? tab("sources", "来源", showSources, onSourcesTab, onCloseSources) : null}
          {readers.map((r) => tab(`reader-${r.url}`, r.title || r.url, showReader && r.url === readerUrl, () => onReaderTab(r.url), () => onCloseReader(r.url)))}
        </div>
        <span className="spacer" />
        {!showSources && !showReader && current && live && !current.libraryFileId ? <span className="live on">实时</span> : null}
        <span className="doc-tools" ref={setToolSlot} />
        {!showSources && !showReader && current && downloads[previewTabKey(current)] ? (
          <a className="icon-btn" href={downloads[previewTabKey(current)]} target="_blank" rel="noreferrer" aria-label={`下载 ${current.filename}`} title="下载">
            <IconDownload size={16} />
          </a>
        ) : showReader ? (
          <a className="icon-btn" href={reader.url} target="_blank" rel="noreferrer noopener" aria-label="在新标签打开" title="在新标签打开">
            <IconExternal size={16} />
          </a>
        ) : null}
      </div>
      <DocToolsSlot.Provider value={toolSlot}>
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
        <div className={`preview-body${current.libraryFileId ? " library-pane" : ""}${office && currentDoc ? " office-pane" : ""}`} key={currentDoc ? `${previewTabKey(current)}:${JSON.stringify(currentDoc.source)}` : previewTabKey(current) + (current.file_id || "")}>
          {(() => {
            const deliveredId = current.kind === "native" ? delivered[current.filename] : undefined;
            const paged = current.libraryFileId || deliveredId ? <LibraryPane fileId={(current.libraryFileId || deliveredId)!} filename={current.filename} /> : current.kind === "native" ? <WorkspacePreview preview={current} /> : current.kind === "pages" ? <Pages preview={current} /> : current.kind === "pdf" ? <PdfPane preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : current.kind === "html" && current.file_id ? <HtmlPane preview={current} /> : null;
            return office && currentDoc ? <NativePane doc={currentDoc} revision={current.version} office={office} reveal={revealOf(current)} fallback={paged} /> : paged;
          })()}
        </div>
      ) : null}
      </DocToolsSlot.Provider>
    </aside>
  );
}
