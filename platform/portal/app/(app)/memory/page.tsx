"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { IconPlus, IconTrash } from "../../../components/icons";
import DreamPanel from "../../../components/memory/dream-panel";
import Switch from "../../../components/switch";
import { api } from "../../../lib/api";
import { timeAgo } from "../../../lib/format";

type Kind = "user" | "memory";
type Entry = {
  id: string;
  kind: Kind;
  content: string;
  source: "user" | "agent" | "nightly";
  updated_at: string;
  confidence?: number;
  invalid_at?: string | null;
  superseded_by?: string | null;
  suppressed?: boolean; // paused: kept here, never shown to the model
  project_id?: string | null;
  status?: "active" | "review";
  evidence_run_ids?: string[];
  source_quote?: string;
  evidence_deleted?: boolean; // the conversation it was learned from has been deleted
};
type DreamSettings = { enabled: boolean; timezone: string | null; window_start: string; window_end: string };
type MemoryDoc = {
  revision: number;
  enabled: boolean;
  auto_save: boolean;
  reference_history: boolean;
  project_only: boolean;
  entries: Entry[];
  limit_chars: number;
  limit_entries: number;
  dream?: DreamSettings;
};
/** An entry as edited on this page; `key` is local, `id` is absent until the API has stored it. */
type Draft = { key: string; id?: string; kind: Kind; content: string; suppressed?: boolean; project_id?: string | null; status?: "active" | "review" };

const ENTRY_CHARS = 1000; // the API's per-entry limit
const GROUPS: { kind: Kind; title: string }[] = [
  { kind: "user", title: "个人偏好" },
  { kind: "memory", title: "长期记忆" },
];

let keys = 0;
const draftOf = (e: Entry): Draft => ({ key: `k${++keys}`, id: e.id, kind: e.kind, content: e.content, suppressed: !!e.suppressed, project_id: e.project_id || null, status: e.status || "active" });

/** What a save would send: blank entries are deletions, text is stored trimmed. */
function payload(draft: Draft[]) {
  return draft
    .filter((d) => d.content.trim())
    .map((d) => ({ ...(d.id ? { id: d.id } : {}), kind: d.kind, content: d.content.trim(), suppressed: !!d.suppressed, project_id: d.project_id || null, status: d.status || "active" }));
}

function sameAsStored(draft: Draft[], stored: Entry[]): boolean {
  const next = payload(draft);
  return (
    next.length === stored.length &&
    next.every((d, i) => d.id === stored[i].id && d.kind === stored[i].kind && d.content === stored[i].content && d.suppressed === !!stored[i].suppressed && d.project_id === (stored[i].project_id || null) && d.status === (stored[i].status || "active"))
  );
}

/**
 * Carry the user's unsaved edits onto a newer stored version (written by the Agent or another tab).
 * Entries the user changed keep their text; untouched ones follow `latest`; entries added elsewhere appear,
 * entries the user deleted stay deleted, and an edited entry deleted elsewhere is kept as a new one.
 */
function rebase(base: Entry[], draft: Draft[], latest: Entry[]): Draft[] {
  const before = new Map(base.map((e) => [e.id, e]));
  const after = new Map(latest.map((e) => [e.id, e]));
  const out: Draft[] = [];
  for (const d of draft) {
    const prior = d.id ? before.get(d.id) : undefined;
    const edited = !prior || prior.kind !== d.kind || prior.content !== d.content || !!prior.suppressed !== !!d.suppressed || (prior.status || "active") !== d.status || (prior.project_id || null) !== d.project_id;
    const now = d.id ? after.get(d.id) : undefined;
    if (!d.id || (now && edited)) out.push(d);
    else if (now) out.push({ ...d, kind: now.kind, content: now.content, suppressed: !!now.suppressed, project_id: now.project_id || null, status: now.status || "active" });
    else if (edited) out.push({ ...d, id: undefined });
  }
  for (const e of latest) if (!before.has(e.id)) out.push(draftOf(e));
  return out;
}

/** The API answers `"<status> <body>"`; a string detail on 422 is a stale revision or entry. */
function problem(e: unknown): { stale: boolean; message: string } {
  const text = String((e as Error)?.message || e);
  const match = /^(\d+) ([\s\S]*)$/.exec(text);
  if (match?.[1] === "422") {
    let detail: unknown;
    try {
      detail = JSON.parse(match[2]).detail;
    } catch {}
    if (typeof detail === "string") return { stale: detail.includes("记忆已更新"), message: detail };
    return { stale: false, message: "有内容为空或超出容量" };
  }
  return { stale: false, message: `没能保存：${text}` };
}

export default function MemoryPage() {
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [sources, setSources] = useState<Record<string, { quote: string; sources: { conversation_id: string | null; title: string }[] }>>({});
  const sourceLoads = useRef(new Map<string, AbortController>());
  const [stored, setStored] = useState<MemoryDoc | null>(null);
  const [draft, setDraft] = useState<Draft[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const storedRef = useRef<MemoryDoc | null>(null);
  const savingRef = useRef(false);
  const dirtyRef = useRef(false);
  const loads = useRef<AbortController | null>(null);
  const generation = useRef(0); // a write or a newer load makes older responses stale

  const dirty = !!stored && !sameAsStored(draft, stored.entries);
  dirtyRef.current = dirty;
  const used = payload(draft).reduce((n, d) => n + d.content.length, 0);
  const limitChars = stored?.limit_chars ?? 12000;
  const limitEntries = stored?.limit_entries ?? 100;
  const full = draft.length >= limitEntries;
  const over = used > limitChars;

  /** Adopt a stored version, carrying unsaved edits onto it. */
  const adopt = useCallback((latest: MemoryDoc) => {
    const base = storedRef.current;
    storedRef.current = latest;
    setStored(latest);
    setDraft((d) => (base ? rebase(base.entries, d, latest.entries) : latest.entries.map(draftOf)));
  }, []);

  const load = useCallback(() => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    const current = ++generation.current;
    return api<MemoryDoc>("/v1/memory", { signal: controller.signal })
      .then((doc) => {
        if (generation.current !== current) return;
        adopt(doc);
        setStale(false);
        setError("");
      })
      .catch((e) => {
        if (generation.current !== current || controller.signal.aborted) return;
        setStale(true);
        setError(`没能读取记忆：${e?.message || e}`);
      });
  }, [adopt]);

  useEffect(() => {
    load();
    const projectLoad = new AbortController();
    api<{ id: string; name: string }[]>("/v1/projects", { signal: projectLoad.signal }).then(setProjects).catch(() => {});
    // the Agent writes memory during chats: pick that up on return, unless the user is mid-edit
    const refresh = () => document.visibilityState === "visible" && !dirtyRef.current && !savingRef.current && load();
    document.addEventListener("visibilitychange", refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      loads.current?.abort();
      projectLoad.abort();
      sourceLoads.current.forEach((c) => c.abort());
      generation.current++;
    };
  }, [load]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** One write at a time. A save shows the stored result as is; a switch keeps unsaved entry edits. */
  async function put(change: Partial<Pick<MemoryDoc, "enabled" | "auto_save" | "reference_history" | "project_only">>, entries: ReturnType<typeof payload>, replace: boolean) {
    const base = storedRef.current;
    if (!base || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    loads.current?.abort();
    const current = ++generation.current;
    try {
      const body = { expected_revision: base.revision, enabled: base.enabled, auto_save: base.auto_save, reference_history: base.reference_history, project_only: base.project_only, ...change, entries };
      const doc = await api<MemoryDoc>("/v1/memory", { method: "PUT", body: JSON.stringify(body) });
      if (generation.current !== current) return;
      if (replace) {
        storedRef.current = doc;
        setStored(doc);
        setDraft(doc.entries.map(draftOf)); // blank entries dropped, new ids assigned
      } else adopt(doc);
      setStale(false);
    } catch (e) {
      if (generation.current !== current) return;
      const p = problem(e);
      setStale(p.stale);
      setError(p.message);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  const toggle = (change: Partial<Pick<MemoryDoc, "enabled" | "auto_save" | "reference_history" | "project_only">>) => put(change, payload(storedRef.current!.entries.map(draftOf)), false);
  const save = () => dirty && !over && put({}, payload(draft), true);

  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    // on the window: deleting a row with the keyboard leaves focus on <body>
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function discard() {
    if (stored) setDraft(stored.entries.map(draftOf));
    setError("");
    setStale(false);
  }

  function add(kind: Kind) {
    const d: Draft = { key: `k${++keys}`, kind, content: "", project_id: scope !== "all" && scope !== "global" ? scope : null, status: "active" };
    setDraft((list) => [...list, d]);
    setFocusKey(d.key);
  }

  const edit = (key: string, content: string) => setDraft((list) => list.map((d) => (d.key === key ? { ...d, content } : d)));
  const remove = (key: string) => setDraft((list) => list.filter((d) => d.key !== key));
  const pause = (key: string) => setDraft((list) => list.map((d) => (d.key === key ? { ...d, suppressed: !d.suppressed } : d)));

  async function inspectSource(id: string) {
    sourceLoads.current.get(id)?.abort();
    const controller = new AbortController();
    sourceLoads.current.set(id, controller);
    try {
      const value = await api<{ quote: string; sources: { conversation_id: string | null; title: string }[] }>(`/v1/memory/${id}/sources`, { signal: controller.signal });
      if (!controller.signal.aborted) setSources((s) => ({ ...s, [id]: value }));
    } catch (e) { if (!controller.signal.aborted) setError(`没能读取来源：${(e as Error).message}`); }
  }

  const byId = new Map((stored?.entries || []).map((e) => [e.id, e]));

  return (
    <main className="page page-narrow memory-page">
      <div className="page-head">
        <h1>记忆</h1>

      </div>
      {error ? (
        <div className="alert error memory-alert" role="alert">
          <span className="grow">{error}</span>
          {stale ? (
            <button className="secondary sm" onClick={load} disabled={saving}>
              重新加载
            </button>
          ) : null}
        </div>
      ) : null}

      {stored === null ? (
        error ? null : <div className="empty">加载中…</div>
      ) : (
        <>
          <section className="card skill-list">
            <div className="skill-row">
              <div className="grow">
                <div className="skill-title">使用记忆</div>
                <div className="skill-desc">关闭后 AI 不读取也不写入，已有内容保留。</div>
              </div>
              <Switch on={stored.enabled} label="使用记忆" disabled={saving} onChange={(enabled) => toggle({ enabled })} />
            </div>
            <div className={`skill-row ${stored.enabled ? "" : "off"}`}>
              <div className="grow">
                <div className="skill-title">自动记忆</div>
                <div className="skill-desc">关闭后只在你明确要求时记住或忘记。</div>
              </div>
              <Switch on={stored.auto_save} label="自动记忆" disabled={saving || !stored.enabled} onChange={(auto_save) => toggle({ auto_save })} />
            </div>
            <div className="skill-row">
              <div className="grow"><div className="skill-title">引用历史对话</div></div>
              <Switch on={stored.reference_history !== false} label="引用历史对话" disabled={saving || !stored.enabled} onChange={(reference_history) => toggle({ reference_history })} />
            </div>
            <div className="skill-row">
              <div className="grow"><div className="skill-title">项目仅使用本项目记忆</div></div>
              <Switch on={!!stored.project_only} label="项目仅使用本项目记忆" disabled={saving} onChange={(project_only) => toggle({ project_only })} />
            </div>
          </section>

          <DreamPanel initial={stored.dream} onChanged={() => load()} />
          <div className="memory-filters">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索记忆" aria-label="搜索记忆" />
            <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="记忆范围">
              <option value="all">全部范围</option><option value="global">账号</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className="secondary sm" aria-pressed={showHistory} onClick={() => setShowHistory(!showHistory)}>{showHistory ? "收起历史" : "查看历史"}</button>
          </div>

          {GROUPS.map((g) => {
            const rows = draft.filter((d) => d.kind === g.kind && (scope === "all" || (d.project_id || "global") === scope)
              && (!query || d.content.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
              && (showHistory || !byId.get(d.id || "")?.invalid_at));
            return (
              <div key={g.kind} className={`memory-group ${stored.enabled ? "" : "off"}`}>
                <div className="section-title">
                  {g.title}
                  {rows.length ? <span className="count">{rows.length}</span> : null}
                  <span className="spacer" />
                  <button className="secondary sm" onClick={() => add(g.kind)} disabled={full || saving}>
                    <IconPlus size={15} /> 新建
                  </button>
                </div>
                <section className="card memory-list">
                  {rows.length === 0 ? <div className="empty memory-empty">还没有内容</div> : null}
                  {rows.map((d) => {
                    const prior = d.id ? byId.get(d.id) : undefined;
                    return (
                      <div key={d.key} className={`memory-row ${prior?.superseded_by || prior?.invalid_at ? "superseded" : ""} ${d.suppressed ? "paused" : ""}`}>
                        <textarea
                          className="memory-text"
                          value={d.content}
                          rows={Math.min(8, Math.max(d.content.split("\n").length, Math.ceil(d.content.length / 48)) || 1)} // where field-sizing is unsupported
                          maxLength={ENTRY_CHARS}
                          readOnly={saving}
                          autoFocus={d.key === focusKey}
                          aria-label={g.title}
                          title={prior ? `${prior.source === "agent" ? "AI 记录" : prior.source === "nightly" ? "夜间整理" : "手动"} · ${timeAgo(prior.updated_at)}${prior.confidence != null && prior.confidence < 0.95 ? ` · 置信度 ${Math.round(prior.confidence * 100)}%` : ""}${prior.superseded_by || prior.invalid_at ? " · 已失效" : ""}` : undefined}
                          onChange={(e) => edit(d.key, e.target.value)}
                        />
                        {d.content.length > ENTRY_CHARS * 0.9 ? <span className="hint memory-count">{d.content.length}/{ENTRY_CHARS}</span> : null}
                        <div className="memory-entry-meta">
                          <select aria-label="条目范围" value={d.project_id || ""} disabled={saving}
                            onChange={(e) => setDraft((list) => list.map((v) => v.key === d.key ? { ...v, project_id: e.target.value || null } : v))}>
                            <option value="">账号</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                          </select>
                          {prior?.invalid_at ? <span>已失效</span> : null}
                          {d.status === "review" ? <button className="secondary sm" onClick={() => setDraft((list) => list.map((v) => v.key === d.key ? { ...v, status: "active" } : v))}>确认使用</button> : null}
                          {prior?.evidence_run_ids?.length ? <button className="secondary sm" onClick={() => inspectSource(prior.id)}>查看来源</button> : null}
                          {d.id && sources[d.id] ? <div className="memory-source">
                            {sources[d.id].quote ? <blockquote>{sources[d.id].quote}</blockquote> : null}
                            {sources[d.id].sources.map((v, i) => v.conversation_id ? <Link key={i} href={`/c/${v.conversation_id}`}>{v.title}</Link> : <span key={i}>{v.title}</span>)}
                          </div> : null}
                        </div>
                        {d.suppressed ? <span className="hint memory-tag">已暂停</span> : null}
                        {prior?.evidence_deleted ? <span className="hint memory-tag" title="来源对话已删除，原话和引用已清除；这条记忆仍然保留，可在此删除">来源已删除</span> : null}
                        <button
                          className="secondary sm memory-pause memory-remove"
                          onClick={() => pause(d.key)}
                          disabled={saving}
                          title={d.suppressed ? "恢复后 AI 会重新参考这条" : "暂停后保留在这里，但 AI 不会参考"}
                        >
                          {d.suppressed ? "恢复" : "暂停"}
                        </button>
                        <button className="icon-btn danger-hover memory-remove" onClick={() => remove(d.key)} disabled={saving} aria-label="删除" title="删除">
                          <IconTrash size={16} />
                        </button>
                      </div>
                    );
                  })}
                </section>
              </div>
            );
          })}

          <div className={`memory-foot ${dirty ? "dirty" : ""}`}>
            <span className={`hint ${over ? "memory-over" : ""}`}>
              {used.toLocaleString()} / {limitChars.toLocaleString()} 字 · {draft.length} / {limitEntries} 条
            </span>
            <span className="spacer" />
            {dirty ? (
              <>
                <button className="secondary sm" onClick={discard} disabled={saving}>
                  放弃更改
                </button>
                <button className="sm" onClick={save} disabled={saving || over}>
                  {saving ? "保存中…" : "保存"}
                </button>
              </>
            ) : null}
          </div>
        </>
      )}
    </main>
  );
}
