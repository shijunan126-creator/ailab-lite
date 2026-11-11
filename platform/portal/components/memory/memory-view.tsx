"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, MemoryItem, MemoryView } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { IconArrowUp, IconBookmark, IconCompass, IconPencil, IconRetry, IconSliders, IconTarget, IconTrash, IconUser } from "../icons";
import RowMenu from "../row-menu";

const POLL_MS = 1500;
const FOLLOW_MS = 10 * 60_000; // a consolidation of several batches fits well inside this
const INSTRUCTION_CHARS = 500; // the API's limit
const CONTENT_CHARS = 200; // the API's limit for one line
const NEARLY_FULL = 0.85; // where the next consolidation starts condensing

/** What each kind of remembered thing is called on the page, in the order the API sends sections. */
export const KIND_TITLE: Record<MemoryItem["kind"], string> = {
  fact: "关于你",
  preference: "偏好",
  project: "在做的事",
  interest: "关注的话题",
  reference: "资料",
};
const KIND_ICON: Record<MemoryItem["kind"], typeof IconUser> = {
  fact: IconUser,
  preference: IconSliders,
  project: IconTarget,
  interest: IconCompass,
  reference: IconBookmark,
};

type Run = {
  id: string;
  status: string;
  trigger?: string;
  error?: string;
  report?: { reply?: string; changes?: { removed?: number }; runs_seen?: number; added?: number; changed?: number; next_id?: string };
};
const DONE = new Set(["applied", "skipped", "failed", "reverted", "evaluated"]);
const scopeQuery = (projectId: string | null) => (projectId ? `?project_id=${encodeURIComponent(projectId)}` : "");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** One scope, followed while a consolidation or an edit is running (started here, in another tab, or at night). */
export function useMemoryView(projectId: string | null, paused = false) {
  const [view, setView] = useState<MemoryView | null>(null);
  const [error, setError] = useState("");
  const loads = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    try {
      const value = await api<MemoryView>(`/v1/memory/view${scopeQuery(projectId)}`, { signal: controller.signal });
      if (controller.signal.aborted) return null;
      setView(value);
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

  const active = view?.active?.id || null;
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

  return { view, error, load };
}

/** "11-03" this year, "2025-11-03" otherwise. */
export function shortDate(value: string | null, now = new Date()): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const md = `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`;
}

/** What a finished "立即整理" did, in one line. */
export function organizeNote(run: Run): string {
  const seen = run.report?.runs_seen || 0;
  const added = run.report?.added || 0;
  const tidied = Math.max(0, (run.report?.changed || 0) - added); // `changed` counts every change, additions included
  const parts = [added > 0 && `新增 ${added} 条`, tidied > 0 && `整理了 ${tidied} 处记忆`].filter(Boolean).join("，");
  if (seen <= 0) return parts || "已整理";
  return `看了 ${seen} 段对话，${parts || "没有需要长期记住的内容"}`;
}

type Change = { content: string };
type Act = { change: (item: MemoryItem, c: Change) => Promise<boolean>; remove: (item: MemoryItem) => void; busy: boolean };

function ItemRow({ item, act, editable }: { item: MemoryItem; act: Act; editable: boolean }) {
  const [draft, setDraft] = useState<string | null>(null); // the text being edited in place
  const actions = [
    { label: "编辑", icon: <IconPencil size={15} />, onSelect: () => setDraft(item.content) },
    "sep" as const,
    { label: "删除", icon: <IconTrash size={15} />, danger: true, onSelect: () => act.remove(item) },
  ];

  async function save() {
    const content = (draft || "").trim();
    if (!content || content === item.content) return setDraft(null);
    if (await act.change(item, { content })) setDraft(null);
  }

  if (draft !== null)
    return (
      <li className="memory-item editing">
        <form
          className="memory-edit"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <textarea
            autoFocus
            rows={2}
            value={draft}
            maxLength={CONTENT_CHARS}
            aria-label="编辑记忆"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setDraft(null);
              else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                save();
              }
            }}
          />
          <div className="memory-edit-actions">
            <button type="button" className="secondary sm" onClick={() => setDraft(null)}>取消</button>
            <button type="submit" className="sm" disabled={act.busy || !draft.trim()}>保存</button>
          </div>
        </form>
      </li>
    );
  return (
    <li className="memory-item">
      <div className="memory-item-text">
        {item.content}
      </div>
      <span className="memory-meta">{shortDate(item.observed_at || item.updated_at)}</span>
      {editable ? <RowMenu label={item.content} actions={actions} disabled={act.busy} /> : null}
    </li>
  );
}

type Turn = { key: number; role: "user" | "ai"; text: string; run?: string; state?: "working" | "applied" | "done" | "failed" | "reverted" };

/**
 * Everything one scope remembers. It is one bounded document the AI reads at the start of every conversation, so the
 * page shows all of it, by kind. The API is the only writer.
 */
export function MemoryPanel({
  projectId,
  title,
  editable = true,
  heading = "h1",
}: {
  projectId: string | null;
  title: string;
  editable?: boolean; // a shared project's lines are changed from the account page
  heading?: "h1" | "none"; // inside a dialog the dialog's own title names the scope
}) {
  const { view, error: loadError, load } = useMemoryView(projectId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState(""); // what the last "立即整理" did
  const [organizing, setOrganizing] = useState(false);
  const [latest, setLatest] = useState(false); // "立即整理" found nothing to do
  const alive = useRef(true);
  const follows = useRef(new Set<AbortController>());
  const thread = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLTextAreaElement | null>(null);
  const keys = useRef(0);

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

  useEffect(() => {
    if (!latest) return;
    const timer = setTimeout(() => setLatest(false), 2500);
    return () => clearTimeout(timer);
  }, [latest]);

  const working = !!view?.active;
  const editing = turns.some((t) => t.state === "working");

  /** Poll a run (and the runs it hands over to) until it and the scope's lease have ended. */
  const settle = useCallback(
    async (first: string, signal: AbortSignal): Promise<Run | null> => {
      let id = first;
      const until = Date.now() + FOLLOW_MS;
      while (Date.now() < until) {
        await sleep(POLL_MS);
        if (signal.aborted) return null;
        let run: Run;
        try {
          run = await api<Run>(`/v1/memory/dreams/${id}`, { signal });
        } catch {
          if (signal.aborted) return null;
          continue; // a missed poll is retried
        }
        if (!DONE.has(run.status)) continue;
        if (run.report?.next_id) {
          id = run.report.next_id;
          continue;
        }
        const refreshed = await api<MemoryView>(`/v1/memory/view${scopeQuery(projectId)}`, { signal }).catch(() => null);
        if (signal.aborted) return null;
        if (!refreshed || refreshed.active) continue;
        return run;
      }
      return null;
    },
    [projectId]
  );

  const track = useCallback(async <T,>(work: (signal: AbortSignal) => Promise<T>) => {
    const controller = new AbortController();
    follows.current.add(controller);
    try {
      return await work(controller.signal);
    } finally {
      follows.current.delete(controller);
    }
  }, []);

  async function organize() {
    setBusy(true);
    setError("");
    setNote("");
    setLatest(false);
    try {
      const started = await api<{ id?: string | null; status: string }>(`/v1/memory/dream/run${scopeQuery(projectId)}`, { method: "POST" });
      if (started.status === "nothing" || !started.id) {
        setLatest(true);
        return;
      }
      setOrganizing(true);
      await load();
      const run = await track((signal) => settle(started.id!, signal));
      if (!alive.current) return;
      if (run?.status === "failed") setError(run.error || "整理未完成，请稍后重试");
      else if (run) setNote(organizeNote(run));
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      if (alive.current) {
        setBusy(false);
        setOrganizing(false);
      }
    }
  }

  const patch = (key: number, change: Partial<Turn>) => setTurns((list) => list.map((t) => (t.key === key ? { ...t, ...change } : t)));

  async function send() {
    const instruction = text.trim();
    if (!instruction || !view || busy || editing || working) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const run = await api<{ id: string }>("/v1/memory/edit", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, instruction, expected_revision: view.revision }),
      });
      const asked = ++keys.current;
      const key = ++keys.current;
      setTurns((list) => [
        ...list.map((t) => (t.state === "applied" ? { ...t, state: "done" as const } : t)), // only the newest change can be undone
        { key: asked, role: "user", text: instruction },
        { key, role: "ai", text: "", run: run.id, state: "working" },
      ]);
      setText("");
      track((signal) => settle(run.id, signal)).then(async (value) => {
        if (!alive.current) return;
        if (!value) return patch(key, { text: "还在处理，稍后回来查看", state: "done" });
        const reply = value.report?.reply || (value.status === "failed" ? value.error || "没能修改，请重试" : "已更新");
        // forgetting is final (every copy is cleared), so only a change without removals offers an undo
        const undoable = value.status === "applied" && !value.report?.changes?.removed;
        patch(key, { text: reply, state: undoable ? "applied" : value.status === "failed" ? "failed" : "done" });
        await load();
      });
    } catch (e) {
      setError(errorMessage(e));
      await load();
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

  async function mutate(request: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await request();
      return true;
    } catch (e) {
      setError(errorMessage(e)); // a stale revision says so; the reload below shows what changed
      return false;
    } finally {
      await load();
      if (alive.current) setBusy(false);
    }
  }

  const change = async (item: MemoryItem, body: Change) =>
    !!view &&
    mutate(() => api(`/v1/memory/items/${item.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: view.revision, ...body }) }));

  async function remove(item: MemoryItem) {
    if (!view) return;
    const ok = await confirmDialog({ title: "删除这条记忆？", body: item.content, action: "删除", danger: true });
    if (ok) mutate(() => api(`/v1/memory/items/${item.id}?expected_revision=${view.revision}`, { method: "DELETE" }));
  }

  async function clear() {
    const ok = await confirmDialog({
      title: projectId ? "清空这个项目的记忆？" : "清空你的记忆？",
      body: projectId ? "只删除本项目的记忆，无法恢复。" : "项目里的记忆不受影响。删除后无法恢复。",
      action: "清空",
      danger: true,
    });
    if (!ok) return;
    setTurns([]);
    mutate(() => api(`/v1/memory/entries${scopeQuery(projectId)}`, { method: "DELETE" }));
  }

  const blocked = busy || editing || working;
  const shownError = error || loadError;
  const act: Act = { change, remove, busy: blocked };
  // newest first inside a kind; the API's order is only stable, not meaningful to a reader
  const time = (item: MemoryItem) => Date.parse(item.observed_at || item.updated_at) || 0;
  // every kind is shown, in the API's order, so the page reads as the same profile whether or not a kind has lines yet
  const sections = (view?.sections || []).map((s) => ({ ...s, items: [...s.items].sort((a, b) => time(b) - time(a) || a.id.localeCompare(b.id)) }));
  const empty = !!view && !sections.some((s) => s.items.length);
  const full = !!view && view.max_chars > 0 && view.used_chars >= view.max_chars * NEARLY_FULL;
  return (
    <div className={`memory-panel ${view && !view.enabled ? "off" : ""}`}>
      <div className="memory-head">
        {heading === "h1" ? <h1>{title}</h1> : <span className="memory-head-gap" />}
        <button className="secondary sm memory-organize" onClick={organize} disabled={blocked || !view?.enabled}>
          {organizing || (working && !editing) ? <span className="draft-spin" aria-hidden /> : <IconRetry size={14} />}
          {organizing || (working && !editing) ? "整理中" : latest ? "已是最新" : "立即整理"}
        </button>
        {editable ? (
          <RowMenu
            label={title}
            disabled={blocked || empty}
            actions={[{ label: "清空", icon: <IconTrash size={15} />, danger: true, onSelect: clear }]}
          />
        ) : null}
      </div>
      {note ? <div className="memory-note" role="status">{note}</div> : full ? <div className="memory-note">快满了，下次整理会合并精简</div> : null}
      {shownError ? <div className="alert error">{shownError}</div> : null}

      {view === null ? (
        shownError ? null : <div className="memory-empty">加载中…</div>
      ) : (
        <div className={`memory-body ${working ? "working" : ""}`} aria-busy={working}>
          {sections.map((s) => {
            const Icon = KIND_ICON[s.kind];
            return (
              <section key={s.kind} className={`memory-section${s.items.length ? "" : " empty"}`}>
                <h3 className="memory-section-title">
                  <Icon size={18} />
                  {KIND_TITLE[s.kind]}
                  {s.items.length ? <span className="memory-count">{s.items.length}</span> : null}
                </h3>
                {s.items.length ? (
                  <ul className="memory-items">
                    {s.items.map((item) => (
                      <ItemRow key={item.id} item={item} act={act} editable={editable} />
                    ))}
                  </ul>
                ) : (
                  <div className="memory-none">暂无</div>
                )}
              </section>
            );
          })}
        </div>
      )}

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

      {editable ? (
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
      ) : null}
    </div>
  );
}
