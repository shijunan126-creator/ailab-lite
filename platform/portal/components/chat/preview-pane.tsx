"use client";

import { useEffect, useMemo, useRef } from "react";
import { Preview, previewUrl } from "../../lib/api";
import { IconDownload } from "../icons";
import Sheets from "../preview/sheets";
import DocumentPages from "../preview/pages";

function Pages({ preview }: { preview: Preview }) {
  const pages = preview.pages || [];
  if (!pages.length || !preview.run_id) return <div className="hint">没有可显示的页面</div>;
  return <DocumentPages pages={pages} src={(name) => previewUrl(preview.run_id!, name)} documentKey={`${preview.run_id}:${preview.filename}`} thumbnails />;
}

export default function PreviewPane({
  previews,
  selected,
  onSelect,
  onClose,
  live,
  downloads,
}: {
  previews: Preview[];
  selected: string | null;
  onSelect: (filename: string) => void;
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
    pane.current?.querySelector<HTMLButtonElement>('[aria-label="关闭预览"]')?.focus();
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("popstate", pop);
      if (mobile && window.history.state?.ailabPreview === marker) window.history.back();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const current = useMemo(() => previews.find((p) => p.filename === selected) || previews[previews.length - 1], [previews, selected]);
  if (!current) return null;
  return (
    <aside ref={pane} className="preview-pane" aria-label="文件预览">
      <div className="preview-head">
        <div className="preview-tabs">
          {previews.map((p) => (
            <button key={p.filename} className={`preview-tab ${p.filename === current.filename ? "on" : ""}`} onClick={() => onSelect(p.filename)}>
              {p.filename}
            </button>
          ))}
        </div>
        <span className="spacer" />
        {live ? <span className="live on">实时</span> : null}
        {downloads[current.filename] ? (
          <a className="btn secondary sm" href={downloads[current.filename]} target="_blank" rel="noreferrer" aria-label={`下载 ${current.filename}`}>
            <IconDownload size={15} />
          </a>
        ) : null}
        <button className="link" onClick={onClose} aria-label="关闭预览">
          ✕
        </button>
      </div>
      <div className="preview-body" key={current.filename}>
        {current.kind === "pages" ? <Pages preview={current} /> : current.kind === "sheets" ? <Sheets src={current.run_id && current.data ? previewUrl(current.run_id, current.data) : null} /> : null}
      </div>
    </aside>
  );
}
