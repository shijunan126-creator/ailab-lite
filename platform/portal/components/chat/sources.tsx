"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { siteOf, splitSources, type Source } from "../../lib/work-process";

const site = (s: Source) => (s.site || siteOf(s.url)).replace(/^www\./, "");

function Mark({ source }: { source: Source }) {
  return <span className="source-mark">{site(source)[0]?.toUpperCase()}</span>;
}

/** Where an answer's sources wait: a few of their marks and one word. */
export function SourcesButton({ sources, open, onOpen }: { sources: Source[]; open?: boolean; onOpen: () => void }) {
  const marks: Source[] = [];
  for (const s of sources) if (marks.length < 3 && !marks.some((m) => site(m) === site(s))) marks.push(s);
  return (
    <button type="button" className={`sources-btn ${open ? "open" : ""}`} aria-label={`来源（${sources.length}）`} aria-expanded={open} onClick={onOpen}>
      <span className="sources-marks">{marks.map((s) => <Mark key={s.url} source={s} />)}</span>
      来源
    </button>
  );
}

function Item({ source }: { source: Source }) {
  return (
    <a className="src-item" href={source.url} target="_blank" rel="noreferrer noopener">
      <span className="src-site"><Mark source={source} />{site(source)}</span>
      <span className="src-title">{source.title || source.url}</span>
    </a>
  );
}

/** The pages the answer cites, in its order, then the others the turn read or found. */
export function SourcesList({ sources, answer, onScroll }: { sources: Source[]; answer: string; onScroll?: (top: number) => void }) {
  const { cited, more } = splitSources(sources, answer);
  return (
    <div className="src-list" onScroll={onScroll ? (e) => onScroll(e.currentTarget.scrollTop) : undefined}>
      {cited.map((s) => <Item key={s.url} source={s} />)}
      {cited.length && more.length ? <div className="src-group">更多</div> : null}
      {more.map((s) => <Item key={s.url} source={s} />)}
    </div>
  );
}

const DRAG_OPEN = 56; // px up from half height that opens the whole sheet
const DRAG_CLOSE = 80; // px down that closes it (or drops a full sheet back to half)

/** Phones: a sheet from the bottom at half height. Dragged up or scrolled it fills the screen; dragged down it steps back, then closes. */
export function SourcesSheet({ sources, answer, onClose }: { sources: Source[]; answer: string; onClose: () => void }) {
  const [full, setFull] = useState(false);
  const [height, setHeight] = useState<number | null>(null); // while dragging
  const sheet = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ y: number; h: number } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const marker = `sources-${Date.now()}`;
    const pop = () => close.current();
    // Back closes the sheet. The entry is added a tick later, so a mount that is undone at once leaves no back() behind to close the next.
    const push = setTimeout(() => {
      window.history.pushState({ ...window.history.state, ailabSheet: marker }, "");
      window.addEventListener("popstate", pop);
    });
    const key = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", key);
    sheet.current?.focus();
    return () => {
      clearTimeout(push);
      window.removeEventListener("popstate", pop);
      window.removeEventListener("keydown", key);
      if (window.history.state?.ailabSheet === marker) window.history.back();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return createPortal(
    <div className="sheet-layer">
      <div className="sheet-scrim" onClick={onClose} />
      <div ref={sheet} className={`sources-sheet ${full ? "full" : ""} ${height !== null ? "dragging" : ""}`} role="dialog" aria-modal="true" aria-label="来源" tabIndex={-1}
        style={height !== null ? { height } : undefined}>
        <div className="sheet-grip"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            drag.current = { y: e.clientY, h: sheet.current!.getBoundingClientRect().height };
          }}
          onPointerMove={(e) => {
            if (drag.current) setHeight(Math.max(96, drag.current.h - (e.clientY - drag.current.y)));
          }}
          onPointerUp={(e) => {
            const start = drag.current;
            drag.current = null;
            setHeight(null);
            if (!start) return;
            const dy = e.clientY - start.y;
            if (dy < -DRAG_OPEN) setFull(true);
            else if (dy > DRAG_CLOSE) full && dy < start.h / 2 ? setFull(false) : onClose();
          }}
          onPointerCancel={() => {
            drag.current = null;
            setHeight(null);
          }}>
          <span className="sheet-handle" />
          <div className="sheet-title">来源</div>
        </div>
        <SourcesList sources={sources} answer={answer} onScroll={(top) => top > 8 && !full && setFull(true)} />
      </div>
    </div>,
    document.body
  );
}
