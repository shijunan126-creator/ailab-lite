"use client";

import { useEffect, useState } from "react";
import type { MentionItem } from "../../lib/api";
import { cachedMentions, fetchMentions, highlightPieces, mentionKey, type MentionScope } from "../../lib/mentions";
import FileTile from "../library/file-tile";
import { IconCheck } from "../icons";

/** Files for the "@query" being typed; null while no "@" is open. Shows the last answer until the next one arrives. */
export function useMentions(query: string | null, scope: MentionScope): { items: MentionItem[]; settled: boolean } {
  const { conversationId, projectId } = scope;
  const [answer, setAnswer] = useState<{ key: string; items: MentionItem[]; failed?: boolean } | null>(null);
  const key = query === null ? null : mentionKey({ conversationId, projectId }, query);
  const cached = query === null ? null : cachedMentions({ conversationId, projectId }, query);

  useEffect(() => {
    if (query === null || key === null || cachedMentions({ conversationId, projectId }, query)) return;
    const abort = new AbortController();
    // the first list on "@" at once; while typing, wait for a short pause
    const timer = setTimeout(
      () =>
        fetchMentions({ conversationId, projectId }, query, abort.signal)
          .then((items) => setAnswer({ key, items }))
          .catch(() => {
            if (!abort.signal.aborted) setAnswer({ key, items: [], failed: true });
          }),
      query.trim() ? 80 : 0,
    );
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [query, key, conversationId, projectId]);

  if (query === null) return { items: [], settled: false };
  if (cached) return { items: cached, settled: true };
  if (answer?.key === key) return { items: answer.items, settled: !answer.failed };
  return { items: answer?.failed ? [] : answer?.items || [], settled: false };
}

function where(f: MentionItem): string {
  if (f.origin === "conversation") return "本对话";
  return (f.delivered && f.conversation_title) || f.project_name || "";
}

export default function MentionMenu({
  items,
  index,
  attached,
  onHover,
  onPick,
}: {
  items: MentionItem[];
  index: number;
  attached: Set<string>;
  onHover: (i: number) => void;
  onPick: (f: MentionItem) => void;
}) {
  return (
    <div className="slash-menu mention-menu" id="mention-menu" role="listbox" aria-label="引用文件">
      {items.length ? (
        items.map((f, i) => {
          const on = attached.has(f.id);
          return (
            <button key={f.id} type="button" role="option" aria-selected={i === index} title={f.filename}
              className={`slash-item mention-item ${i === index ? "on" : ""} ${on ? "picked" : ""}`}
              onMouseMove={() => onHover(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(f)}>
              <FileTile file={f} size={26} />
              <span className="slash-label mention-name">
                {highlightPieces(f.filename, f.highlight || []).map((p, j) => (p.hit ? <mark key={j}>{p.text}</mark> : p.text))}
              </span>
              {on ? <IconCheck size={14} /> : <span className="mention-where">{where(f)}</span>}
            </button>
          );
        })
      ) : (
        <div className="menu-empty" role="presentation">没有匹配的文件</div>
      )}
    </div>
  );
}
