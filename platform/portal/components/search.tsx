"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { api, Conversation, SearchHit } from "../lib/api";
import { timeAgo } from "../lib/format";
import { IconFolder, IconMessage, IconSearch } from "./icons";

/** Opens the search from anywhere (the sidebar entry, ⌘K / Ctrl+K). */
export const OPEN_SEARCH = "ailab:open-search";

function Snippet({ hit }: { hit: SearchHit }) {
  // offsets count Unicode code points, not UTF-16 units: an emoji before a hit must not shift it
  const chars = Array.from(hit.snippet);
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const [start, end] of [...(hit.highlight || [])].sort((a, b) => a[0] - b[0])) {
    if (start < at || end <= start) continue;
    parts.push(chars.slice(at, start).join(""), <mark key={start}>{chars.slice(start, end).join("")}</mark>);
    at = end;
  }
  parts.push(chars.slice(at).join(""));
  return <span className="search-snippet">{parts}</span>;
}

/** Every conversation, by title and by what was said in it. Empty query: the recent ones. */
export default function SearchDialog({ recent, onClose }: { recent: Conversation[]; onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  async function search(query: string, after: string | null, controller: AbortController, append: boolean) {
    const found: SearchHit[] = [];
    try {
      // Bound each interaction, retaining the continuation instead of reporting an unfinished scan as empty.
      for (let i = 0; i < 6; i++) {
        const res: { items: SearchHit[]; next_cursor: string | null } = await api(
          `/v1/search?q=${encodeURIComponent(query.slice(0, 200))}&limit=20${after ? `&cursor=${encodeURIComponent(after)}` : ""}`,
          { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]) }
        );
        if (controller.signal.aborted) return;
        found.push(...res.items.filter((h) => !found.some((x) => x.conversation_id === h.conversation_id)));
        after = res.next_cursor;
        if (!after || found.length >= 12) break;
      }
      if (controller.signal.aborted) return;
      setHits((previous) => append ? [...(previous || []), ...found.filter((h) => !previous?.some((x) => x.conversation_id === h.conversation_id))] : found);
      setCursor(after);
      setError("");
    } catch (err: any) {
      if (!controller.signal.aborted) setError(String(err?.message || err));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  // searched as the user types: a later query cancels the one still on its way
  useEffect(() => {
    const query = q.trim();
    setActive(0);
    setCursor(null);
    setError("");
    if (!query) {
      setHits(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setHits([]);
    setLoading(true);
    const timer = setTimeout(() => search(query, null, controller, false), 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [q]);

  const rows: { key: string; href: string; title: string; hit?: SearchHit; when: string; project: boolean }[] = hits
    ? hits.map((h) => ({
        key: `${h.conversation_id}:${h.run_id || ""}`,
        href: `/c/${h.conversation_id}${h.run_id && h.match !== "title" ? `?run=${h.run_id}` : ""}`,
        title: h.title || "新对话",
        hit: h,
        when: h.updated_at,
        project: !!h.project_id,
      }))
    : recent.slice(0, 8).map((c) => ({ key: c.id, href: `/c/${c.id}`, title: c.title || "新对话", when: c.updated_at, project: !!c.project_id }));

  function go(index: number) {
    const row = rows[index];
    if (!row) return;
    onClose();
    router.push(row.href);
  }

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return createPortal(
    <div className="modal-scrim search-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal search-dialog" role="dialog" aria-label="搜索对话">
        <div className="search-field">
          <IconSearch size={18} />
          <input
            ref={input}
            type="text"
            value={q}
            placeholder="搜索对话"
            aria-label="搜索对话"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((i) => Math.min(rows.length - 1, i + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => Math.max(0, i - 1));
              } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                go(active);
              }
            }}
          />
          {loading ? <span className="chip-spin" aria-hidden="true" /> : null}
        </div>
        <div className="search-results" ref={list} role="listbox" aria-label="结果">
          {rows.map((row, i) => (
            <button
              key={row.key}
              data-index={i}
              role="option"
              aria-selected={i === active}
              className={`search-row ${i === active ? "on" : ""}`}
              onMouseMove={() => setActive(i)}
              onClick={() => go(i)}
            >
              <span className="search-icon">{row.project ? <IconFolder size={15} /> : <IconMessage size={15} />}</span>
              <span className="search-text">
                <span className="search-title">{row.title}</span>
                {row.hit && row.hit.match !== "title" ? <Snippet hit={row.hit} /> : null}
              </span>
              <span className="search-when">{timeAgo(row.when)}</span>
            </button>
          ))}
          {cursor ? <button className="link" disabled={loading} onClick={() => {
            const controller = controllerRef.current;
            if (!controller || controller.signal.aborted || loading) return;
            setLoading(true);
            search(q.trim(), cursor, controller, true);
          }}>继续搜索</button> : null}
          {hits && !hits.length && !loading && !cursor ? <div className="search-empty">没有找到相关对话</div> : null}
          {error ? <div className="alert error">{error}</div> : null}
        </div>
      </div>
    </div>,
    document.body
  );
}
