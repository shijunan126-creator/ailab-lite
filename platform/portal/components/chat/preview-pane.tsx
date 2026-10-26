"use client";

import { useEffect, useMemo, useRef } from "react";
import { Preview, previewUrl } from "../../lib/api";
import { IconArrowLeft, IconDownload, IconX } from "../icons";
import Sheets from "../preview/sheets";
import DocumentPages from "../preview/pages";
import type { Source } from "../../lib/work-process";
import RunDetails from "./run-details";
import { SourcesList } from "./sources";

function Pages({ preview }: { preview: Preview }) {
  const pages = preview.pages || [];
  if (!pages.length || !preview.run_id) return <div className="hint">没有可显示的页面</div>;
  return <DocumentPages pages={pages} src={(name) => previewUrl(preview.run_id!, name)} documentKey={`${preview.run_id}:${preview.filename}`} thumbnails />;
}

export type PaneSources = { runId: string; sources: Source[]; answer: string };

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
  detailRun,
  detailsOn,
  onDetailsTab,
  onCloseDetails,
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
  detailRun: string | null;
  detailsOn: boolean;
  onDetailsTab: () => void;
  onCloseDetails: () => void;
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
    pane.current?.querySelector<HTMLButtonElement>(".pane-tab.on button")?.focus({ preventScroll: true });
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("popstate", pop);
      if (mobile && window.history.state?.ailabPreview === marker) window.history.back();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  const current = useMemo(() => previews.find((p) => p.filename === selected) || previews[previews.length - 1], [previews, selected]);
  const showDetails = !!detailRun && (detailsOn || (!current && !sources));
  const showSources = !showDetails && !!sources && (sourcesOn || !current);
  if (!current && !sources && !detailRun) return null;
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
          {previews.map((p) => tab(p.filename, p.filename, !showSources && !showDetails && p.filename === current?.filename, () => onSelect(p.filename), () => onCloseFile(p.filename)))}
          {detailRun ? tab("details", "详情", showDetails, onDetailsTab, onCloseDetails) : null}
          {sources ? tab("sources", "来源", showSources, onSourcesTab, onCloseSources) : null}
        </div>
        <span className="spacer" />
        {!showSources && !showDetails && current ? (
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
      {showDetails ? (
        <div className="preview-body details-body" key={`details-${detailRun}`}><RunDetails key={detailRun} id={detailRun!} /></div>
      ) : showSources ? (
        <div className="preview-body sources-body" key={`sources-${sources!.runId}`}>
          <SourcesList sources={sources!.sources} answer={sources!.answer} />
        </div>
      ) : current ? (
        <div className="preview-body" key={current.filename}>
          {current.kind === "pages" ? <Pages preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : null}
        </div>
      ) : null}
    </aside>
  );
}
