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

function Item({ source, onRead }: { source: Source; onRead?: (source: Source) => void }) {
  return (
    <a
      className="src-item"
      href={source.url}
      target="_blank"
      rel="noreferrer noopener"
      title={onRead ? "在侧栏打开" : undefined}
      onClick={onRead ? (e) => { e.preventDefault(); onRead(source); } : undefined}
    >
      <span className="src-site"><Mark source={source} />{site(source)}</span>
      <span className="src-title">{source.title || source.url}</span>
    </a>
  );
}

/** The pages the answer cites, in its order, then the others the turn read or found. */
export function SourcesList({ sources, answer, onRead, onScroll }: { sources: Source[]; answer: string; onRead?: (source: Source) => void; onScroll?: (top: number) => void }) {
  const { cited, more } = splitSources(sources, answer);
  return (
    <div className="src-list" onScroll={onScroll ? (e) => onScroll(e.currentTarget.scrollTop) : undefined}>
      {cited.map((s) => <Item key={s.url} source={s} onRead={onRead} />)}
      {cited.length && more.length ? <div className="src-group">更多</div> : null}
      {more.map((s) => <Item key={s.url} source={s} onRead={onRead} />)}
    </div>
  );
}

const DRAG_OPEN = 56; // px up from half height that opens the whole sheet
const DRAG_CLOSE = 80; // px down that closes it (or drops a full sheet back to half)

/** Phones: a sheet from the bottom at half height. Dragged up or scrolled it fills the screen; dragged down — by the grip, or by the list once it is at its top — it steps back, then closes. */
export function SourcesSheet({ sources, answer, onClose }: { sources: Source[]; answer: string; onClose: () => void }) {
  const [full, setFull] = useState(false);
  const [height, setHeight] = useState<number | null>(null); // while dragging
  const sheet = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ y: number; h: number } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const isFull = useRef(full);
  isFull.current = full;

  const grab = (y: number) => {
    drag.current = { y, h: sheet.current!.getBoundingClientRect().height };
  };
  const pull = (y: number) => {
    if (drag.current) setHeight(Math.max(96, drag.current.h - (y - drag.current.y)));
  };
  const release = (y: number | null) => {
    const start = drag.current;
    drag.current = null;
    setHeight(null);
    if (!start || y === null) return;
    const dy = y - start.y;
    if (dy < -DRAG_OPEN) setFull(true);
    else if (dy > DRAG_CLOSE) isFull.current && dy < start.h / 2 ? setFull(false) : close.current();
  };
  const gesture = useRef({ grab, pull, release });
  gesture.current = { grab, pull, release };

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

  // A downward swipe on the list that starts at its top moves the sheet instead of overscrolling. Touch events, not pointer
  // events: the browser claims a pointer for scrolling (pointercancel) before we know the direction, and only a non-passive
  // touchmove can keep it.
  useEffect(() => {
    const el = sheet.current!;
    let start: { y: number; list: HTMLElement } | null = null;
    let dragging = false;
    const down = (e: TouchEvent) => {
      const list = (e.target as Element).closest?.(".src-list") as HTMLElement | null;
      start = list && e.touches.length === 1 && list.scrollTop <= 0 ? { y: e.touches[0].clientY, list } : null;
      dragging = false;
    };
    const move = (e: TouchEvent) => {
      if (!start) return;
      const y = e.touches[0].clientY;
      if (!dragging) {
        if (y - start.y < 0 || start.list.scrollTop > 0) return void (start = null); // an ordinary scroll
        if (y - start.y < 4) return;
        dragging = true;
        gesture.current.grab(start.y);
      }
      e.preventDefault();
      gesture.current.pull(y);
    };
    const up = (e: TouchEvent) => {
      if (dragging) gesture.current.release(e.type === "touchend" ? e.changedTouches[0].clientY : null);
      start = null;
      dragging = false;
    };
    el.addEventListener("touchstart", down, { passive: true });
    el.addEventListener("touchmove", move, { passive: false });
    el.addEventListener("touchend", up);
    el.addEventListener("touchcancel", up);
    return () => {
      el.removeEventListener("touchstart", down);
      el.removeEventListener("touchmove", move);
      el.removeEventListener("touchend", up);
      el.removeEventListener("touchcancel", up);
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
            grab(e.clientY);
          }}
          onPointerMove={(e) => pull(e.clientY)}
          onPointerUp={(e) => release(e.clientY)}
          onPointerCancel={() => release(null)}>
          <span className="sheet-handle" />
          <div className="sheet-title">来源</div>
        </div>
        <SourcesList sources={sources} answer={answer} onScroll={(top) => top > 8 && !full && setFull(true)} />
      </div>
    </div>,
    document.body
  );
}
