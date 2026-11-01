"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, MemorySummary } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { IconArrowUp, IconChevronRight, IconRetry, IconTrash } from "../icons";
import Modal from "../library/modal";
import Markdown from "../markdown";
import RowMenu from "../row-menu";

const POLL_MS = 1500;
const FOLLOW_MS = 10 * 60_000; // a consolidation of several batches plus the summaries fits well inside this
const INSTRUCTION_CHARS = 500; // the API's limit

type Run = { id: string; status: string; trigger?: string; error?: string; report?: { reply?: string; changes?: { removed?: number } } };
const DONE = new Set(["applied", "skipped", "failed", "reverted", "evaluated"]);
const scopeQuery = (projectId: string | null) => (projectId ? `?project_id=${encodeURIComponent(projectId)}` : "");

/** The summary of one scope, followed while a consolidation or an edit is running (started here, in another tab, or at night). */
export function useMemorySummary(projectId: string | null, paused = false) {
  const [summary, setSummary] = useState<MemorySummary | null>(null);
  const [error, setError] = useState("");
  const loads = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    try {
      const value = await api<MemorySummary>(`/v1/memory/summary${scopeQuery(projectId)}`, { signal: controller.signal });
      if (controller.signal.aborted) return null;
      setSummary(value);
      setError("");
      return value;
    } catch (e) {
      if (!controller.signal.aborted) setError(errorMessage(e));
      return null;
    }
  }, [projectId]);

  useEffect(() => {
    if (paused) return;
    load();
    const refresh = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      loads.current?.abort();
    };
  }, [load, paused]);

  const active = summary?.active?.id || null;
  useEffect(() => {
    if (!active || paused) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const until = Date.now() + FOLLOW_MS;
    const tick = async () => {
      if (stopped || Date.now() > until) return;
      const next = await load();
      if (!stopped && next?.active) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [active, load, paused]);

  return { summary, error, load };
}

/** Headings and list marks removed: the first lines of the summary, read as one paragraph. */
function plain(text: string): string {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.replace(/^([-*+]|\d+\.)\s+/, "").replace(/[*_`]/g, ""))
    .map((line) => (/[。；！？.;!?]$/.test(line) ? line : `${line}；`))
    .join("")
    .replace(/；$/, "");
}

/** The entry on a page: the beginning of the summary; the whole of it, and changing it, in the dialog. */
export function MemoryCard({ projectId, title, disabled }: { projectId: string | null; title: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const { summary, load } = useMemorySummary(projectId, open);
  const preview = summary ? plain(summary.text) : "";
  const working = !!summary?.active;
  return (
    <>
      <div className={`mrow memory-card ${disabled ? "off" : ""}`}>
        <button className="mrow-main" onClick={() => setOpen(true)} aria-label={title}>
          <span className="mrow-title"><span>{title}</span></span>
          {summary ? (
            <span className={`memory-preview ${preview ? "" : "empty"}`}>
              {preview || (working ? "正在整理…" : summary.entries ? "整理后在这里查看" : "还没有记忆")}
            </span>
          ) : null}
        </button>
        <span className="mrow-trail memory-chevron" aria-hidden>
          <IconChevronRight size={16} />
        </span>
      </div>
      {open ? <MemoryDialog projectId={projectId} title={title} onClose={() => (setOpen(false), load())} /> : null}
    </>
  );
}

type Turn = { key: number; role: "user" | "ai"; text: string; run?: string; state?: "working" | "applied" | "done" | "failed" | "reverted" };

/**
 * The whole summary of one scope. The user changes it by saying what to remember, correct or forget; the API turns
 * that into changes to the facts and writes the summary again, so the text never drifts from what the AI uses.
 */
export function MemoryDialog({ projectId, title, onClose, top }: { projectId: string | null; title: string; onClose: () => void; top?: (reload: () => void) => React.ReactNode }) {
  const { summary, error: loadError, load } = useMemorySummary(projectId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [latest, setLatest] = useState(false); // "立即整理" found nothing to do
  const started = useRef(false);
  const alive = useRef(true);
  const follows = useRef(new Set<AbortController>());
  const thread = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLTextAreaElement | null>(null);
  const keys = useRef(0);
  const lastActive = useRef<string | null>(null); // the consolidation last seen running

  useEffect(() => {
    alive.current = true;
    const pending = follows.current;
    return () => {
      alive.current = false;
      pending.forEach((c) => c.abort());
    };
  }, []);

  useEffect(() => {
    thread.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [turns]);

  const working = !!summary?.active;
  const editing = turns.some((t) => t.state === "working");

  const organize = useCallback(async () => {
    setBusy(true);
    setError("");
    setLatest(false);
    try {
      const started = await api<{ id?: string; status: string }>(`/v1/memory/dream/run${scopeQuery(projectId)}`, { method: "POST" });
      if (started.status === "nothing") setLatest(true);
      else if (started.id) lastActive.current = started.id; // it may already be over by the time the summary is read
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [load, projectId]);

  // the facts changed since the text was written (a chat remembered something, or it was never written): bring it up to date once
  useEffect(() => {
    if (!summary || started.current) return;
    started.current = true;
    if (summary.enabled && summary.stale && !summary.active) organize();
  }, [summary, organize]);

  // a consolidation (started here, on opening, or elsewhere) that ended in failure says so; edits report in their own turn
  useEffect(() => {
    if (!summary) return;
    if (summary.active) {
      lastActive.current = summary.active.id;
      return;
    }
    const ended = lastActive.current;
    lastActive.current = null;
    if (!ended) return;
    api<Run>(`/v1/memory/dreams/${ended}`)
      .then((run) => {
        if (alive.current && run.status === "failed" && run.trigger !== "edit") setError(run.error || "整理未完成，请稍后重试");
      })
      .catch(() => {}); // the summary itself is already shown; a missed check only hides the reason
  }, [summary]);

  useEffect(() => {
    if (!latest) return;
    const timer = setTimeout(() => setLatest(false), 2500);
    return () => clearTimeout(timer);
  }, [latest]);

  const patch = (key: number, change: Partial<Turn>) => setTurns((list) => list.map((t) => (t.key === key ? { ...t, ...change } : t)));

  /** Wait for an edit to end; its reply says what changed. */
  async function follow(run: string, key: number) {
    const controller = new AbortController();
    follows.current.add(controller);
    const until = Date.now() + FOLLOW_MS;
    try {
      while (Date.now() < until) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (controller.signal.aborted) return;
        let value: Run;
        try {
          value = await api<Run>(`/v1/memory/dreams/${run}`, { signal: controller.signal });
        } catch {
          if (controller.signal.aborted) return;
          continue; // a missed poll is retried
        }
        if (!DONE.has(value.status)) continue;
        const refreshed = await api<MemorySummary>(`/v1/memory/summary${scopeQuery(projectId)}`, { signal: controller.signal }).catch(() => null);
        if (controller.signal.aborted) return;
        if (!refreshed || refreshed.active) continue;
        const reply = value.report?.reply || (value.status === "failed" ? value.error || "没能修改，请重试" : "已更新");
        // forgetting is final (every copy is cleared), so only a change without removals offers an undo
        const undoable = value.status === "applied" && !value.report?.changes?.removed;
        patch(key, { text: reply, state: undoable ? "applied" : value.status === "failed" ? "failed" : "done" });
        await load();
        return;
      }
      patch(key, { text: "还在处理，稍后回来查看", state: "done" });
    } finally {
      follows.current.delete(controller);
    }
  }

  async function send() {
    const instruction = text.trim();
    if (!instruction || busy || editing || working) return;
    setBusy(true);
    setError("");
    try {
      const run = await api<{ id: string }>("/v1/memory/summary/edit", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, instruction }),
      });
      const asked = ++keys.current;
      const key = ++keys.current;
      setTurns((list) => [
        ...list.map((t) => (t.state === "applied" ? { ...t, state: "done" as const } : t)), // only the newest change can be undone
        { key: asked, role: "user", text: instruction },
        { key, role: "ai", text: "", run: run.id, state: "working" },
      ]);
      setText("");
      follow(run.id, key);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      if (alive.current) setBusy(false);
      field.current?.focus();
    }
  }

  async function undo(turn: Turn) {
    if (!turn.run) return;
    setBusy(true);
    setError("");
    try {
      await api(`/v1/memory/dreams/${turn.run}/revert`, { method: "POST" });
      patch(turn.key, { state: "reverted" });
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    const ok = await confirmDialog({
      title: projectId ? "清空这个项目的记忆？" : "清空你的记忆？",
      body: projectId ? "只删除本项目的记忆，无法恢复。" : "项目里的记忆不受影响。删除后无法恢复。",
      action: "清空",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      await api(`/v1/memory/entries${scopeQuery(projectId)}`, { method: "DELETE" });
      setTurns([]);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const blocked = busy || editing || working;
  const shownError = error || loadError;
  return (
    <Modal
      title={title}
      onClose={onClose}
      className="memory-dialog"
      actions={
        <>
          <button className="secondary sm memory-organize" onClick={organize} disabled={blocked || !summary}>
            {working && !editing ? <span className="draft-spin" aria-hidden /> : <IconRetry size={14} />}
            {working && !editing ? "整理中" : latest ? "已是最新" : "立即整理"}
          </button>
          <RowMenu
            label={title}
            disabled={blocked || !summary?.entries}
            actions={[{ label: "清空", icon: <IconTrash size={15} />, danger: true, onSelect: clear }]}
          />
        </>
      }
      footer={
        <form
          className="memory-ask"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            ref={field}
            rows={1}
            value={text}
            maxLength={INSTRUCTION_CHARS}
            placeholder="告诉 AI 要记住、修改或忘记什么"
            aria-label="修改记忆"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button type="submit" className="icon-btn memory-send" aria-label="发送" disabled={!text.trim() || blocked}>
            <IconArrowUp size={16} />
          </button>
        </form>
      }
    >
      {top?.(load)}
      {shownError ? <div className="alert error">{shownError}</div> : null}
      <div className={`memory-summary ${working ? "working" : ""}`} aria-busy={working}>
        {summary === null ? (
          shownError ? null : <div className="memory-summary-empty">加载中…</div>
        ) : summary.text ? (
          <Markdown text={summary.text} />
        ) : (
          <div className="memory-summary-empty">{working ? "正在整理…" : summary.entries ? "点“立即整理”生成摘要" : "还没有记忆"}</div>
        )}
      </div>
      {turns.length ? (
        <div className="memory-thread" ref={thread} aria-live="polite">
          {turns.map((t) =>
            t.role === "user" ? (
              <div key={t.key} className="memory-turn user">{t.text}</div>
            ) : (
              <div key={t.key} className={`memory-turn ai ${t.state}`}>
                {t.state === "working" ? <span className="memory-typing" aria-label="正在修改"><i /><i /><i /></span> : <span>{t.text}</span>}
                {t.state === "applied" ? (
                  <button className="link sm" onClick={() => undo(t)} disabled={busy}>撤销</button>
                ) : t.state === "reverted" ? (
                  <span className="memory-turn-note">已撤销</span>
                ) : null}
              </div>
            )
          )}
        </div>
      ) : null}
    </Modal>
  );
}
