"use client";

import { useState } from "react";
import { QueuedMessage } from "../../lib/api";
import { IconCornerDownRight, IconPencil, IconQueue, IconX } from "../icons";
import { fromPayload } from "../../lib/message-parts";
import MessageText from "./message-text";

/** Messages written while an answer is running, right above the composer. Each goes after the answer in turn;
 * "插入" hands it to the running answer at its next step instead. */
export default function QueueStrip({
  queue,
  paused,
  running,
  onSteer,
  onEdit,
  onRemove,
  onResume,
}: {
  queue: QueuedMessage[];
  paused: boolean;
  running: boolean; // an answer is being written that could take a message now
  onSteer: (id: string) => Promise<void>;
  onEdit: (id: string, prompt: string) => Promise<boolean>;
  onRemove: (id: string) => Promise<void>;
  onResume: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const waiting = queue.filter((q) => q.state === "queued");
  if (!waiting.length) return null;

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusy(id);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={`queue-strip ${paused ? "paused" : ""}`} role="list" aria-label="待发送">
      {paused ? (
        <div className="queue-paused">
          <span>已暂停</span>
          <span className="spacer" />
          <button className="link" onClick={() => act("resume", onResume)} disabled={busy === "resume"}>
            继续发送
          </button>
        </div>
      ) : null}
      {waiting.map((q) => (
        <div key={q.id} className={`queue-row ${q.steer ? "steer" : ""}`} role="listitem">
          {q.steer ? <IconCornerDownRight size={14} /> : <IconQueue size={14} />}
          {editing?.id === q.id ? (
            <input
              className="queue-edit"
              autoFocus
              value={editing.text}
              aria-label="修改待发送的消息"
              onChange={(e) => setEditing({ id: q.id, text: e.target.value })}
              onKeyDown={async (e) => {
                if (e.key === "Escape") setEditing(null);
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  const text = editing.text.trim();
                  if (!text || text === q.prompt) return setEditing(null);
                  if (await onEdit(q.id, text)) setEditing(null);
                }
              }}
              onBlur={() => setEditing(null)}
            />
          ) : (
            <span className="queue-text" title={q.prompt}><MessageText parts={fromPayload(q.prompt, q.skills)} /></span>
          )}
          {running && !q.steer && !paused ? (
            <button className="link queue-steer" disabled={busy === q.id} onClick={() => act(q.id, () => onSteer(q.id))} title="交给正在进行的回答">
              插入
            </button>
          ) : null}
          <button className="icon-btn act" aria-label="修改" title="修改" onClick={() => setEditing({ id: q.id, text: q.prompt })}>
            <IconPencil size={14} />
          </button>
          <button className="icon-btn act" aria-label="移除" title="移除" disabled={busy === q.id} onClick={() => act(q.id, () => onRemove(q.id))}>
            <IconX size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
