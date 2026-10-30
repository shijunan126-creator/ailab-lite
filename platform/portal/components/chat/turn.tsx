"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, downloadUrl, errorMessage, EventItem, quotaMessage, Run } from "../../lib/api";
import { formatBytes, isTerminal } from "../../lib/format";
import { placeMenu } from "../../lib/menu-place";
import { startingState } from "../../lib/ui-state";
import { splitTurn, turnSources, type Source } from "../../lib/work-process";
import CopyButton from "../copy-button";
import { IconBranch, IconChevronRight, IconCornerDownRight, IconDownload, IconFile, IconPencil, IconRetry, IconTemplate } from "../icons";
import { ExtTile } from "../library/file-tile";
import FileMenu from "../library/file-menu";
import Answer from "./answer";
import { answerForCopy } from "../genui/markdown-bridge";
import { useSmoothText } from "../motion/smooth-text";
import { useFileViewer } from "../preview/file-viewer";

export { reduceEvent } from "../../lib/work-process";
export type { Item } from "../../lib/work-process";
import { reduceEvent, type Item } from "../../lib/work-process";
import { SourcesButton } from "./sources";
import WorkProcess from "./work-process";
import { useViewport } from "../viewport-block";
/** Runs that ended before budgets paused instead: their stored English reason, said plainly. */
function runError(error: string): string {
  const steps = /^agent exceeded (\d+) steps$/.exec(error);
  if (steps) return `本轮用完了 ${steps[1]} 步`;
  const timeout = /^run timeout after (\d+)s$/.exec(error);
  if (timeout) return `本轮超过了 ${Math.round(Number(timeout[1]) / 60)} 分钟`;
  return error;
}

const PREVIEWABLE = /\.(pptx|xlsx|docx|pdf|html?|xhtml)$/i;
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
      setError(errorMessage(err));
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

/** A run paused for quota: how long until it can go on, kept current while it is on screen. */
function QuotaWait({ pause }: { pause: { window: string; resets_at: string } }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  return <span className="resume-why" title={new Date(pause.resets_at).toLocaleString("zh-CN", { hour12: false })}>{quotaMessage(pause.window, pause.resets_at, now)}</span>;
}

const MENU_W = 184;

/** Continue from this turn in a new conversation. A conversation that already worked in files can only go on working. */
function ForkButton({ modes, onFork }: { modes: ForkMode[]; onFork: (mode: ForkMode) => Promise<void> }) {
  const [at, setAt] = useState<React.CSSProperties | null>(null);
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
          setAt(placeMenu(button.current!.getBoundingClientRect(), MENU_W, 12 + modes.length * 38, { align: "start", prefer: "above" }));
        }}>
        <IconBranch size={15} />
      </button>
      {at ? createPortal(
        <div ref={menu} className="pop-menu fixed" role="menu" style={at}>
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

export default memo(function Turn({
  run,
  items,
  live,
  last,
  fresh,
  modelLabel,
  previewKey,
  onPreview,
  onPreviewFile,
  onResume,
  onAnswer,
  onRetry,
  onEdit,
  onSwitch,
  locked,
  forkModes,
  onFork,
  projectId,
  sourcesOpen,
  onSources,
  onOpenLink,
}: {
  run: Run;
  items: Item[] | undefined;
  live: boolean;
  last?: boolean;
  fresh?: boolean;
  modelLabel: string;
  previewKey: (run: Run, filename: string) => string | null;
  onPreview: (filename: string) => void;
  onPreviewFile: (file: { file_id: string; filename: string }) => void;
  onResume?: (run: Run) => void;
  onAnswer?: (run: Run, questionId: string, answer: string) => Promise<void>;
  onRetry?: (run: Run) => Promise<void>;
  onEdit?: (run: Run, prompt: string) => Promise<boolean>;
  onSwitch?: (runId: string) => Promise<void>;
  locked?: boolean; // an answer is being written: versions and edits wait for it
  forkModes?: ForkMode[];
  onFork?: (run: Run, mode: ForkMode) => Promise<void>;
  projectId?: string | null; // where this conversation's files are kept
  sourcesOpen?: boolean;
  onSources?: (run: Run, sources: Source[], answer: string) => void;
  onOpenLink?: (url: string, title: string) => void;
}) {
  const [thoughtsExpanded, setThoughtsExpanded] = useState<Set<number>>(new Set());
  const toggleThought = (step: number) => setThoughtsExpanded((previous) => {
    const next = new Set(previous);
    if (next.has(step)) next.delete(step); else next.add(step);
    return next;
  });
  const [processExpanded, setProcessExpanded] = useState<boolean | null>(null);
  const [history, setHistory] = useState<Item[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [moreHistory, setMoreHistory] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const historyCursor = useRef(0);
  const historyRequest = useRef<AbortController | null>(null);
  useEffect(() => () => historyRequest.current?.abort(), []);
  const files = useFileViewer();
  const shown = items ?? history;
  const terminal = isTerminal(run.status, !!run.result?.file_wait);
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
      if (!controller.signal.aborted) setHistoryError(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setLoadingHistory(false);
      historyRequest.current = null;
    }
  }
  const { process, answer: streamed } = useMemo(() => splitTurn(shown), [shown]);
  // the confirmed answer once the run is over; until then whatever the model has written after its last step
  const answer = (terminal && run.status !== "interrupted" ? run.summary?.trim() : "") || streamed;
  const visibleAnswer = useSmoothText(answer, live && !terminal);
  // a plain chat answer with nothing behind it needs no process box; while waiting for the first word it is the indicator
  const showProcess = !chat || process.length > 0 || (!terminal && !answer) || (shown == null && terminal && !!run.work_ms && run.work_ms >= 1000);
  // kept on the run once it ends; from the stream (or loaded history) until then
  const eventSources = useMemo(() => turnSources(shown), [shown]);
  const sources = run.result?.sources?.length ? run.result.sources : eventSources;
  const quotaPause = run.status === "interrupted" && run.result?.pause?.kind === "quota" ? run.result.pause : null;
  const done = run.status === "succeeded" || run.status === "failed" || run.status === "canceled";
  // what the user added while this answer ran: from the turn itself once it is saved, from the stream until then
  const steered = [...(run.steered || [])];
  for (const it of shown || []) if (it.kind === "steer" && !steered.some((x) => x.id === it.id)) steered.push({ id: it.id, prompt: it.prompt });

  const viewport = useViewport(!terminal || editing !== null || !!files.viewer, 240);
  return (
    <div ref={viewport.ref} style={viewport.style} data-mounted={viewport.visible} className={`turn ${fresh ? "fresh" : ""}`} id={`turn-${run.id}`}>
      {viewport.visible ? <>
      <div className={`msg user ${editing !== null ? "editing" : ""}`}>
        {editing !== null ? (
          <EditBox value={editing} onChange={setEditing} onCancel={() => setEditing(null)} onSubmit={async () => {
            const prompt = editing.trim();
            if (!prompt || !onEdit) return;
            if (prompt === run.prompt.trim()) return setEditing(null);
            if (await onEdit(run, prompt)) setEditing(null);
          }} />
        ) : (
          <div className="bubble">{run.prompt}</div>
        )}
        {run.options?.skill ? (
          <div className="attach-row">
            <span className="attach-chip">{SKILL_LABEL[run.options.skill] || run.options.skill_title || run.options.skill}</span>
            {run.template ? (
              <span className="attach-chip" title={run.template.name}>
                <IconTemplate size={13} /> {run.template.name}
              </span>
            ) : null}
          </div>
        ) : null}
        {run.input_files?.length ? (
          <div className="attach-row">
            {run.input_files.map((f) => (
              <button key={f.file_id} className="attach-chip open" onClick={() => onPreviewFile(f)}>
                <IconFile size={13} /> {f.filename}
              </button>
            ))}
          </div>
        ) : null}
        {steered.map((x) => (
          <div key={x.id} className="bubble steer">
            <IconCornerDownRight size={13} />
            <span>{x.prompt}</span>
          </div>
        ))}
        {editing === null ? (
          <div className="msg-actions">
            <CopyButton text={run.prompt} label="复制消息" />
            {onEdit && done && !locked ? (
              <button type="button" className="icon-btn act" aria-label="编辑消息" title="编辑" onClick={() => setEditing(run.prompt)}>
                <IconPencil size={15} />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="msg assistant">
        <div className="avatar-ai">A</div>
        <div className="assistant-body">
          {showProcess ? <WorkProcess thoughtsExpanded={thoughtsExpanded} onToggleThought={toggleThought} expanded={processExpanded} onExpanded={setProcessExpanded} run={run} items={shown == null ? null : process} live={live}
            loading={loadingHistory} error={historyError} more={moreHistory} onLoad={loadHistory} renderAsk={(item) => <Ask item={item}
              live={(!terminal || run.status === "interrupted") && !!onAnswer}
              onAnswer={(value) => onAnswer!(run, item.id, value)} />} /> : null}
          {answer ? <Answer virtual text={visibleAnswer} streaming={live && !terminal} sources={sources} onOpenLink={onOpenLink} runId={run.id} uiState={run.ui_state} saveState /> : null}
          {run.status === "failed" && run.error ? <div className="alert error">出错了：{runError(run.error)}</div> : null}
          {run.resumable && onResume ? (
            <div className="resume">
              <button className="secondary sm" onClick={() => onResume(run)}>
                继续
              </button>
              {quotaPause ? <QuotaWait pause={quotaPause} /> : run.error ? <span className="resume-why">{run.error}</span> : null}
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
                  <FileMenu fileId={a.file_id} filename={a.filename} projectId={projectId} />
                </div>
              ))}
            </div>
          ) : null}

          {terminal ? (
            <div className={`turn-meta ${last ? "last" : ""}`}>
              {run.versions && run.versions.count > 1 && onSwitch ? (
                <span className="versions" role="group" aria-label="版本">
                  <button type="button" className="icon-btn act prev" aria-label="上一个版本" disabled={locked || switching || run.versions.index <= 1}
                    onClick={async () => {
                      setSwitching(true);
                      try { await onSwitch(run.versions!.ids[run.versions!.index - 2]); } finally { setSwitching(false); }
                    }}>
                    <IconChevronRight size={14} />
                  </button>
                  <span className="versions-n">{run.versions.index}/{run.versions.count}</span>
                  <button type="button" className="icon-btn act" aria-label="下一个版本" disabled={locked || switching || run.versions.index >= run.versions.count}
                    onClick={async () => {
                      setSwitching(true);
                      try { await onSwitch(run.versions!.ids[run.versions!.index]); } finally { setSwitching(false); }
                    }}>
                    <IconChevronRight size={14} />
                  </button>
                </span>
              ) : null}
              {answer ? <CopyButton text={() => answerForCopy(answer, (i) => startingState(run.id, run.ui_state)?.blocks[String(i)])} label="复制回答" /> : null}
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
              {sources.length && onSources ? <SourcesButton sources={sources} open={sourcesOpen} onOpen={() => onSources(run, sources, answer)} /> : null}
              <span className="meta-text">{modelLabel}</span>
              {run.usage?.prompt_tokens ? <span className="meta-text">{(run.usage.prompt_tokens || 0) + (run.usage.completion_tokens || 0)} tokens</span> : null}
            </div>
          ) : null}
        </div>
      </div>
      {files.viewer}
      </> : null}
    </div>
  );
});

/** Editing a sent message in place: Enter sends it as a new version, Escape keeps the old one. */
function EditBox({ value, onChange, onCancel, onSubmit }: { value: string; onChange: (v: string) => void; onCancel: () => void; onSubmit: () => Promise<void> }) {
  const area = useRef<HTMLTextAreaElement | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(320, el.scrollHeight)}px`;
  }, [value]);
  async function submit() {
    if (busy) return;
    setBusy(true);
    try {
      await onSubmit();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="edit-box">
      <textarea ref={area} value={value} rows={1} aria-label="编辑消息" onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }} />
      <div className="edit-actions">
        <button type="button" className="secondary sm" onClick={onCancel} disabled={busy}>取消</button>
        <button type="button" className="sm" onClick={submit} disabled={busy || !value.trim()}>发送</button>
      </div>
    </div>
  );
}
