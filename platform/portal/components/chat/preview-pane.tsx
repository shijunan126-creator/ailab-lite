"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Preview, previewUrl } from "../../lib/api";
import { IconDownload } from "../icons";
import Sheets from "../preview/sheets";

function Pages({ preview }: { preview: Preview }) {
  const pages = preview.pages || [];
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex((i) => Math.min(i, Math.max(0, pages.length - 1))), [pages.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.target as HTMLElement)?.closest(".preview-pane")) return;
      if ((e.target as HTMLElement)?.closest("textarea, input")) return;
      if (e.key === "ArrowRight") setIndex((i) => Math.min(pages.length - 1, i + 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pages.length]);
  if (!pages.length || !preview.run_id) return <div className="hint">没有可显示的页面</div>;
  const runId = preview.run_id;
  return (
    <div className="pages">
      <div className="page-stage">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={previewUrl(runId, pages[index])} alt={`第 ${index + 1} 页`} />
        <div className="page-nav">
          <button className="secondary sm" disabled={index === 0} onClick={() => setIndex(index - 1)} aria-label="上一页">
            ‹
          </button>
          <span className="hint">
            {index + 1} / {pages.length}
          </span>
          <button className="secondary sm" disabled={index >= pages.length - 1} onClick={() => setIndex(index + 1)} aria-label="下一页">
            ›
          </button>
        </div>
      </div>
      <div className="thumbs">
        {pages.map((p, i) => (
          <button key={p} className={`thumb ${i === index ? "on" : ""}`} onClick={() => setIndex(i)} aria-label={`第 ${i + 1} 页`}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={previewUrl(runId, p)} alt="" loading="lazy" />
            <span>{i + 1}</span>
          </button>
        ))}
      </div>
    </div>
  );
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
