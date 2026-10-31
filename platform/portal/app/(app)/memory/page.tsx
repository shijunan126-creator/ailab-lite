"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { IconCheck, IconFolder, IconPause, IconPlay, IconPlus, IconQuote, IconTrash } from "../../../components/icons";
import RowMenu, { RowAction } from "../../../components/row-menu";
import DreamPanel from "../../../components/memory/dream-panel";
import Select from "../../../components/select";
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

  const scopeName = (id?: string | null) => (id ? projects.find((p) => p.id === id)?.name || "项目" : "账号");
  const setEntry = (key: string, patch: Partial<Draft>) => setDraft((list) => list.map((v) => (v.key === key ? { ...v, ...patch } : v)));

  return (
    <main className="page mpage memory-page">
      <div className="mpage-head">
        <h1>记忆</h1>
        {stored ? (
          <span className={`hint ${over ? "memory-over" : ""}`}>
            {used.toLocaleString()} / {limitChars.toLocaleString()} 字 · {draft.length} / {limitEntries} 条
          </span>
        ) : null}
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
        error ? null : <div className="mlist"><div className="mlist-empty">加载中…</div></div>
      ) : (
        <>
          <section className="msection">
            <div className="mlist">
              <div className="mrow">
                <div className="mrow-main">
                  <span className="mrow-title"><span>使用记忆</span></span>
                  <span className="mrow-desc">关闭后 AI 不读取也不写入，已有内容保留。</span>
                </div>
                <div className="mrow-trail"><Switch on={stored.enabled} label="使用记忆" disabled={saving} onChange={(enabled) => toggle({ enabled })} /></div>
              </div>
              <div className={`mrow ${stored.enabled ? "" : "off"}`}>
                <div className="mrow-main">
                  <span className="mrow-title"><span>自动记忆</span></span>
                  <span className="mrow-desc">关闭后只在你明确要求时记住或忘记。</span>
                </div>
                <div className="mrow-trail"><Switch on={stored.auto_save} label="自动记忆" disabled={saving || !stored.enabled} onChange={(auto_save) => toggle({ auto_save })} /></div>
              </div>
              <div className={`mrow ${stored.enabled ? "" : "off"}`}>
                <div className="mrow-main"><span className="mrow-title"><span>引用历史对话</span></span></div>
                <div className="mrow-trail"><Switch on={stored.reference_history !== false} label="引用历史对话" disabled={saving || !stored.enabled} onChange={(reference_history) => toggle({ reference_history })} /></div>
              </div>
              <div className="mrow">
                <div className="mrow-main"><span className="mrow-title"><span>项目仅使用本项目记忆</span></span></div>
                <div className="mrow-trail"><Switch on={!!stored.project_only} label="项目仅使用本项目记忆" disabled={saving} onChange={(project_only) => toggle({ project_only })} /></div>
              </div>
            </div>
          </section>

          <DreamPanel initial={stored.dream} onChanged={() => load()} />

          <div className="memory-filters">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索记忆" aria-label="搜索记忆" />
            <Select value={scope} onChange={setScope} aria-label="记忆范围"
              options={[{ value: "all", label: "全部范围" }, { value: "global", label: "账号" }, ...projects.map((p) => ({ value: p.id, label: p.name }))]} />
            <button className="secondary sm" aria-pressed={showHistory} onClick={() => setShowHistory(!showHistory)}>{showHistory ? "收起历史" : "查看历史"}</button>
          </div>

          {GROUPS.map((g) => {
            const rows = draft.filter((d) => d.kind === g.kind && (scope === "all" || (d.project_id || "global") === scope)
              && (!query || d.content.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
              && (showHistory || !byId.get(d.id || "")?.invalid_at));
            return (
              <section key={g.kind} className={`msection memory-group ${stored.enabled ? "" : "off"}`}>
                <div className="msection-head">
                  <h2>{g.title}</h2>
                  {rows.length ? <span className="meta">{rows.length}</span> : null}
                  <div className="actions">
                    <button className="link sm" onClick={() => add(g.kind)} disabled={full || saving}>
                      <IconPlus size={14} /> 新建
                    </button>
                  </div>
                </div>
                <div className="mlist">
                  {rows.length === 0 ? <div className="mlist-empty">还没有内容</div> : null}
                  {rows.map((d) => {
                    const prior = d.id ? byId.get(d.id) : undefined;
                    const gone = !!(prior?.superseded_by || prior?.invalid_at);
                    const meta = [
                      scopeName(d.project_id),
                      prior ? `${prior.source === "agent" ? "对话中记录" : prior.source === "nightly" ? "夜间整理" : "手动"} · ${timeAgo(prior.updated_at)}` : "",
                      gone ? "已失效" : "",
                      d.suppressed ? "已暂停" : "",
                      d.status === "review" ? "待确认" : "",
                      prior?.evidence_deleted ? "来源已删除" : "",
                    ].filter(Boolean);
                    const actions: RowAction[] = [
                      ...(d.status === "review" ? [{ label: "确认使用", icon: <IconCheck size={15} />, onSelect: () => setEntry(d.key, { status: "active" }) }] : []),
                      ...(prior?.evidence_run_ids?.length ? [{ label: "查看来源", icon: <IconQuote size={15} />, onSelect: () => inspectSource(prior.id) }] : []),
                      { label: d.suppressed ? "恢复" : "暂停", icon: d.suppressed ? <IconPlay size={15} /> : <IconPause size={15} />, onSelect: () => pause(d.key) },
                      "sep",
                      { heading: "范围" },
                      { label: "账号", icon: <IconFolder size={15} />, checked: !d.project_id, onSelect: () => setEntry(d.key, { project_id: null }) },
                      ...projects.map((p) => ({ label: p.name, icon: <IconFolder size={15} />, checked: d.project_id === p.id, onSelect: () => setEntry(d.key, { project_id: p.id }) })),
                      "sep",
                      { label: "删除", icon: <IconTrash size={15} />, danger: true, onSelect: () => remove(d.key) },
                    ];
                    return (
                      <div key={d.key} className={`mrow memory-row ${gone ? "superseded" : ""} ${d.suppressed ? "paused" : ""}`}>
                        <div className="mrow-main">
                          <textarea
                            className="memory-text"
                            value={d.content}
                            rows={Math.min(8, Math.max(d.content.split("\n").length, Math.ceil(d.content.length / 44)) || 1)} // where field-sizing is unsupported
                            maxLength={ENTRY_CHARS}
                            readOnly={saving}
                            autoFocus={d.key === focusKey}
                            aria-label={g.title}
                            onChange={(e) => edit(d.key, e.target.value)}
                          />
                          <span className="memory-entry-meta">
                            {meta.join(" · ")}
                            {d.content.length > ENTRY_CHARS * 0.9 ? ` · ${d.content.length}/${ENTRY_CHARS}` : ""}
                          </span>
                          {d.id && sources[d.id] ? (
                            <div className="memory-source">
                              {sources[d.id].quote ? <blockquote>{sources[d.id].quote}</blockquote> : null}
                              {sources[d.id].sources.map((v, i) => v.conversation_id ? <Link key={i} href={`/c/${v.conversation_id}`}>{v.title}</Link> : <span key={i}>{v.title}</span>)}
                            </div>
                          ) : null}
                        </div>
                        <div className="mrow-trail">
                          <RowMenu label={d.content.slice(0, 24) || g.title} actions={actions} disabled={saving} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })}

          {dirty ? (
            <div className="memory-foot">
              <span className="hint">有未保存的修改</span>
              <span className="spacer" />
              <button className="secondary sm" onClick={discard} disabled={saving}>
                放弃更改
              </button>
              <button className="sm" onClick={save} disabled={saving || over}>
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </main>
  );
}
