"use client";

import { useEffect, useRef, useState } from "react";
import { api, downloadUrl, EventItem, Run } from "../../lib/api";
import { formatBytes, isTerminal } from "../../lib/format";
import { IconDownload, IconFile } from "../icons";
import Markdown from "../markdown";
import { useFileViewer } from "../preview/file-viewer";

export { reduceEvent } from "../../lib/work-process";
export type { Item } from "../../lib/work-process";
import { reduceEvent, type Item } from "../../lib/work-process";
import WorkProcess from "./work-process";
const PREVIEWABLE = /\.(pptx|xlsx|docx|pdf)$/i;
const SKILL_LABEL: Record<string, string> = { pptx: "PPT 生成", xlsx: "Excel 表格" };

/** The agent stopped to ask. Answered here or from the composer; the run continues on its own afterwards. */
function Ask({ item, live, onAnswer }: { item: Extract<Item, { kind: "ask" }>; live: boolean; onAnswer: (value: string) => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const answered = item.answer !== undefined;
  async function answer(value: string) {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await onAnswer(value.trim());
    } catch (err: any) {
      setError(String(err?.message || err));
      setBusy(false);
    }
  }
  return (
    <div className={`ask ${answered ? "answered" : ""}`}>
      <div className="ask-q">{item.question}</div>
      {answered ? (
        <div className="ask-a">{item.answer}</div>
      ) : live ? (
        <>
          {item.options.length ? (
            <div className="ask-options">
              {item.options.map((o) => (
                <button key={o} className="secondary sm" disabled={busy} onClick={() => answer(o)}>
                  {o}
                </button>
              ))}
            </div>
          ) : null}
          {item.allowText ? (
            <form
              className="ask-form"
              onSubmit={(e) => {
                e.preventDefault();
                answer(text);
              }}
            >
              <input type="text" value={text} onChange={(e) => setText(e.target.value)} placeholder={item.options.length ? "或者直接写下你的回答" : "你的回答"} disabled={busy} />
              <button type="submit" className="sm" disabled={busy || !text.trim()}>
                回答
              </button>
            </form>
          ) : null}
          {error ? <div className="alert error">{error}</div> : null}
        </>
      ) : null}
    </div>
  );
}

export default function Turn({
  run,
  items,
  live,
  modelName,
  previewKey,
  onPreview,
  onResume,
  onAnswer,
}: {
  run: Run;
  items: Item[] | undefined;
  live: boolean;
  modelName: (id: string) => string;
  previewKey: (run: Run, filename: string) => string | null;
  onPreview: (filename: string) => void;
  onResume?: (run: Run) => void;
  onAnswer?: (run: Run, questionId: string, answer: string) => Promise<void>;
}) {
  const [history, setHistory] = useState<Item[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [moreHistory, setMoreHistory] = useState(false);
  const historyCursor = useRef(0);
  const historyRequest = useRef<AbortController | null>(null);
  useEffect(() => () => historyRequest.current?.abort(), []);
  const files = useFileViewer();
  const shown = items ?? history;
  const terminal = isTerminal(run.status);

  async function loadHistory() {
    if (historyRequest.current) return;
    const controller = new AbortController();
    historyRequest.current = controller;
    setLoadingHistory(true);
    setHistoryError("");
    try {
      const events = await api<EventItem[]>(`/v1/runs/${run.id}/events/history?after=${historyCursor.current}&limit=200`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setHistory((previous) => events.reduce(reduceEvent, previous || []));
      if (events.length) historyCursor.current = events[events.length - 1].seq;
      setMoreHistory(events.length === 200);
    } catch (error: any) {
      if (!controller.signal.aborted) setHistoryError(String(error?.message || error));
    } finally {
      if (!controller.signal.aborted) setLoadingHistory(false);
      historyRequest.current = null;
    }
  }
  const answer = terminal ? run.summary?.trim() : "";
  const lastText = shown?.findLastIndex((item) => item.kind === "text") ?? -1;
  const process = shown?.filter((item, index) => !(index === lastText && item.kind === "text" && item.text.trim() === answer));

  return (
    <div className="turn">
      <div className="msg user">
        <div className="bubble">{run.prompt}</div>
        {run.options?.skill ? (
          <div className="attach-row">
            <span className="attach-chip">{SKILL_LABEL[run.options.skill] || run.options.skill_title || run.options.skill}</span>
          </div>
        ) : null}
        {run.input_files?.length ? (
          <div className="attach-row">
            {run.input_files.map((f, _, all) => (
              <button key={f.file_id} className="attach-chip open" onClick={() => files.open(all.map((x) => x.file_id), f.file_id)}>
                <IconFile size={13} /> {f.filename}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="msg assistant">
        <div className="avatar-ai">A</div>
        <div className="assistant-body">
          <WorkProcess run={run} items={process} loading={loadingHistory} error={historyError}
            more={moreHistory} onLoad={loadHistory} renderAsk={(item) => <Ask item={item}
              live={(!terminal || run.status === "interrupted") && !!onAnswer}
              onAnswer={(value) => onAnswer!(run, item.id, value)} />} />
          {answer ? <Markdown text={answer} /> : null}
          {run.status === "failed" && run.error ? <div className="alert error">出错了：{run.error}</div> : null}
          {run.resumable && onResume ? (
            <div className="resume">
              <button className="secondary sm" onClick={() => onResume(run)}>
                继续
              </button>
            </div>
          ) : null}

          {run.artifacts?.length ? (
            <div className="files">
              {run.artifacts.map((a) => (
                <div key={a.id} className="file-card">
                  <span className="icon">
                    <IconFile size={18} />
                  </span>
                  <div className="grow">
                    <div className="name" title={a.filename}>
                      {a.filename}
                    </div>
                    <div className="meta">{formatBytes(a.size)}</div>
                  </div>
                  <button
                    className="secondary sm"
                    onClick={() => {
                      // the live pane follows a file through later turns; anything else opens on its own
                      const key = PREVIEWABLE.test(a.filename) ? previewKey(run, a.filename) : null;
                      if (key) onPreview(key);
                      else files.open(run.artifacts!.map((x) => x.file_id), a.file_id);
                    }}
                  >
                    预览
                  </button>
                  <a className="btn secondary sm" href={downloadUrl(a.download_url)} target="_blank" rel="noreferrer" aria-label={`下载 ${a.filename}`}>
                    <IconDownload size={15} /> 下载
                  </a>
                </div>
              ))}
            </div>
          ) : null}

          {terminal ? (
            <div className="turn-meta">
              <span>{modelName(run.model)}</span>
              {run.usage?.prompt_tokens ? <span>{(run.usage.prompt_tokens || 0) + (run.usage.completion_tokens || 0)} tokens</span> : null}
              <a href={`/runs/${run.id}`}>详情</a>
            </div>
          ) : null}
        </div>
      </div>
      {files.viewer}
    </div>
  );
}
