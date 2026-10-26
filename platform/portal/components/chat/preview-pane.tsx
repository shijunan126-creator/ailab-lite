"use client";

import { useEffect, useMemo, useRef } from "react";
import { downloadUrl, Preview, previewUrl } from "../../lib/api";
import { IconDownload, IconX } from "../icons";
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
  const showSources = !!sources && (sourcesOn || !current);
  if (!current && !sources) return null;
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
      {showSources ? (
        <div className="preview-body sources-body" key={`sources-${sources!.runId}`}>
          <SourcesList sources={sources!.sources} answer={sources!.answer} />
        </div>
      ) : current ? (
        <div className="preview-body" key={`${current.filename}:${current.file_id || ""}`}>
          {current.kind === "pages" ? <Pages preview={current} /> : current.kind === "pdf" ? <PdfPane preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : current.kind === "html" && current.file_id ? <HtmlPane preview={current} /> : null}
        </div>
      ) : null}
    </aside>
  );
}
