"use client";

import { useEffect, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { answer, current, subscribe } from "../lib/dialog";

/** Shows the question confirmDialog() asked, above everything else (a dialog or the file viewer it came from). */
export default function DialogHost() {
  const ask = useSyncExternalStore(subscribe, current, () => null);

  useEffect(() => {
    if (!ask) return;
    // captured first and stopped: Escape answers this question only, not the dialog underneath
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      answer(ask.id, false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [ask]);

  if (!ask) return null;
  return createPortal(
    <div className="modal-scrim confirm-scrim" onMouseDown={(e) => e.target === e.currentTarget && answer(ask.id, false)}>
      <div className="modal confirm" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby={ask.body ? "confirm-body" : undefined}>
        <h2 id="confirm-title">{ask.title}</h2>
        {ask.body ? <p id="confirm-body">{ask.body}</p> : null}
        <div className="confirm-actions">
          <button className="secondary" onClick={() => answer(ask.id, false)}>
            取消
          </button>
          <button className={ask.danger ? "danger" : ""} autoFocus onClick={() => answer(ask.id, true)}>
            {ask.action || "确定"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
