"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ConversationDetail, errorMessage, Run } from "../../../lib/api";
import { mergeTurns } from "../../../lib/conversation-pages";
import { formatDate } from "../../../lib/format";
import Markdown from "../../../components/markdown";

const MAX_PAGES = 30; // 600 turns: enough for any conversation worth printing, bounded either way

/** A conversation laid out for paper; the browser's print dialog saves it as PDF. */
export default function PrintConversation() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      let page = await api<ConversationDetail>(`/v1/conversations/${id}?limit=20`, { signal: controller.signal });
      let turns: Run[] = page.turns;
      for (let i = 0; page.has_more && turns.length && i < MAX_PAGES; i++) {
        page = await api<ConversationDetail>(`/v1/conversations/${id}?limit=20&before=${encodeURIComponent(turns[0].id)}`, { signal: controller.signal });
        turns = mergeTurns(page.turns, turns);
      }
      setDetail({ ...page, turns });
    })().catch((err) => !controller.signal.aborted && setError(errorMessage(err)));
    return () => controller.abort();
  }, [id]);

  // once laid out (fonts included), straight to the print dialog
  useEffect(() => {
    if (!detail) return;
    document.title = detail.title || "对话";
    let stale = false;
    document.fonts.ready.then(() => !stale && requestAnimationFrame(() => window.print()));
    return () => {
      stale = true;
    };
  }, [detail]);

  if (error) return <main className="print-page"><div className="alert error">{error}</div></main>;
  if (!detail) return <main className="print-page"><div className="pr-loading"><span /><span /><span /></div></main>;
  return (
    <main className="print-page">
      <header className="print-head">
        <h1>{detail.title || "对话"}</h1>
        <div className="print-meta">{formatDate(detail.created_at)}</div>
        <button className="print-again" onClick={() => window.print()}>导出 PDF</button>
      </header>
      {detail.turns.map((t) => (
        <section key={t.id} className="print-turn">
          <div className="print-q">{t.prompt}</div>
          {t.input_files?.length ? <div className="print-files">{t.input_files.map((f) => f.filename).join("、")}</div> : null}
          <div className="print-a">
            {t.summary?.trim() ? <Markdown text={t.summary} /> : t.error ? <p className="print-error">{t.error}</p> : null}
            {t.artifacts?.length ? <div className="print-files">{t.artifacts.map((a) => a.filename).join("、")}</div> : null}
          </div>
        </section>
      ))}
    </main>
  );
}
