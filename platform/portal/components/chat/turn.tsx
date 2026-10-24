"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, downloadUrl, EventItem, Run } from "../../lib/api";
import { formatBytes, isTerminal } from "../../lib/format";
import { siteOf, splitTurn, turnSources } from "../../lib/work-process";
import CopyButton from "../copy-button";
import { IconBranch, IconDownload, IconFile, IconRetry } from "../icons";
import { ExtTile } from "../library/file-tile";
import Markdown from "../markdown";
import { useFileViewer } from "../preview/file-viewer";

export { reduceEvent } from "../../lib/work-process";
export type { Item } from "../../lib/work-process";
import { reduceEvent, type Item } from "../../lib/work-process";
import WorkProcess from "./work-process";
const PREVIEWABLE = /\.(pptx|xlsx|docx|pdf)$/i;
const SKILL_LABEL: Record<string, string> = { pptx: "PPT 生成", xlsx: "Excel 表格" };

export type ForkMode = "chat" | "work";

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

const MENU_W = 184;

/** Continue from this turn in a new conversation. A conversation that already worked in files can only go on working. */
function ForkButton({ modes, onFork }: { modes: ForkMode[]; onFork: (mode: ForkMode) => Promise<void> }) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!at) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && (menu.current?.contains(e.target as Node) || button.current?.contains(e.target as Node))) return;
      setAt(null);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setAt(null);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);
  async function fork(mode: ForkMode) {
    setAt(null);
    setBusy(true);
    try {
      await onFork(mode);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button ref={button} type="button" className={`icon-btn act ${at ? "open" : ""}`} aria-label="从这里分支" title="从这里分支" aria-expanded={!!at} disabled={busy}
        onClick={() => {
          if (at) return setAt(null);
          const r = button.current!.getBoundingClientRect();
          const height = 12 + modes.length * 38;
          const above = r.top - height - 6 > 8;
          setAt({ top: above ? r.top - height - 6 : r.bottom + 6, left: Math.min(Math.max(8, r.left), window.innerWidth - MENU_W - 8) });
        }}>
        <IconBranch size={15} />
      </button>
      {at ? createPortal(
        <div ref={menu} className="pop-menu fixed" role="menu" style={{ top: at.top, left: at.left, width: MENU_W }}>
          {modes.map((mode) => (
            <button key={mode} className="menu-item" role="menuitem" onClick={() => fork(mode)}>
              <IconBranch size={15} /> {mode === "chat" ? "分支为对话" : "分支为工作"}
            </button>
          ))}
        </div>,
        document.body
      ) : null}
    </>
  );
}

export default function Turn({
  run,
  items,
  live,
  last,
  fresh,
  modelName,
  previewKey,
  onPreview,
  onResume,
  onAnswer,
  onRetry,
  forkModes,
  onFork,
}: {
  run: Run;
  items: Item[] | undefined;
  live: boolean;
  last?: boolean;
  fresh?: boolean;
  modelName: (id: string) => string;
  previewKey: (run: Run, filename: string) => string | null;
  onPreview: (filename: string) => void;
  onResume?: (run: Run) => void;
  onAnswer?: (run: Run, questionId: string, answer: string) => Promise<void>;
  onRetry?: (run: Run) => Promise<void>;
  forkModes?: ForkMode[];
  onFork?: (run: Run, mode: ForkMode) => Promise<void>;
}) {
  const [history, setHistory] = useState<Item[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [moreHistory, setMoreHistory] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const historyCursor = useRef(0);
  const historyRequest = useRef<AbortController | null>(null);
  useEffect(() => () => historyRequest.current?.abort(), []);
  const files = useFileViewer();
  const shown = items ?? history;
  const terminal = isTerminal(run.status);
  const chat = run.options?.mode === "chat";

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
  const { process, answer: streamed } = splitTurn(shown);
  // the confirmed answer once the run is over; until then whatever the model has written after its last step
  const answer = (terminal && run.status !== "interrupted" ? run.summary?.trim() : "") || streamed;
  // a plain chat answer with nothing behind it needs no process box; while waiting for the first word it is the indicator
  const showProcess = !chat || process.length > 0 || (!terminal && !answer) || (shown == null && terminal && !!run.work_ms && run.work_ms >= 1000);
  const sources = turnSources(shown);
  const done = run.status === "succeeded" || run.status === "failed" || run.status === "canceled";

  return (
    <div className={`turn ${fresh ? "fresh" : ""}`}>
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
        <div className="msg-actions">
          <CopyButton text={run.prompt} label="复制消息" />
        </div>
      </div>

      <div className="msg assistant">
        <div className="avatar-ai">A</div>
        <div className="assistant-body">
          {showProcess ? <WorkProcess run={run} items={shown == null ? null : process} live={live}
            loading={loadingHistory} error={historyError} more={moreHistory} onLoad={loadHistory} renderAsk={(item) => <Ask item={item}
              live={(!terminal || run.status === "interrupted") && !!onAnswer}
              onAnswer={(value) => onAnswer!(run, item.id, value)} />} /> : null}
          {answer ? <Markdown text={answer} streaming={live && !terminal} /> : null}
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
                  <ExtTile filename={a.filename} size={38} />
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
                    <IconDownload size={15} /> <span className="dl-label">下载</span>
                  </a>
                </div>
              ))}
            </div>
          ) : null}

          {sources.length && terminal ? (
            <div className="sources" aria-label="来源">
              {sources.slice(0, 8).map((s) => (
                <a key={s.url} className="source" href={s.url} target="_blank" rel="noreferrer noopener" title={s.title || s.url}>
                  <span className="source-mark">{(s.site || siteOf(s.url))[0]?.toUpperCase()}</span>
                  <span className="source-name">{s.title || s.site || siteOf(s.url)}</span>
                </a>
              ))}
              {sources.length > 8 ? <span className="source more">+{sources.length - 8}</span> : null}
            </div>
          ) : null}

          {terminal ? (
            <div className={`turn-meta ${last ? "last" : ""}`}>
              {answer ? <CopyButton text={answer} label="复制回答" /> : null}
              {last && done && onRetry ? (
                <button type="button" className={`icon-btn act ${retrying ? "busy" : ""}`} aria-label="重新生成" title="重新生成" disabled={retrying}
                  onClick={async () => {
                    setRetrying(true);
                    try {
                      await onRetry(run);
                    } finally {
                      setRetrying(false);
                    }
                  }}>
                  <IconRetry size={15} />
                </button>
              ) : null}
              {done && onFork && forkModes?.length ? <ForkButton modes={forkModes} onFork={(mode) => onFork(run, mode)} /> : null}
              <span className="meta-text">{modelName(run.model)}</span>
              {run.usage?.prompt_tokens ? <span className="meta-text">{(run.usage.prompt_tokens || 0) + (run.usage.completion_tokens || 0)} tokens</span> : null}
              <Link className="meta-text" href={`/runs/${run.id}`}>详情</Link>
            </div>
          ) : null}
        </div>
      </div>
      {files.viewer}
    </div>
  );
}
