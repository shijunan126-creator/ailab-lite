"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { IconClock, IconPencil, IconPlus, IconSend, IconTrash } from "../../../components/icons";
import Modal from "../../../components/library/modal";
import { api, errorMessage, Schedule, Skill } from "../../../lib/api";
import { formatDate, statusLabel } from "../../../lib/format";
import { useProjects } from "../../../lib/projects";

const LIMIT = 5; // the API enforces the same
const DAYS = ["一", "二", "三", "四", "五", "六", "日"];
type Kind = Schedule["schedule"]["kind"];
type Draft = Pick<Schedule, "title" | "prompt" | "mode" | "skill" | "project_id"> & { kind: Kind; time: string; days: number[] };

function when(s: Schedule["schedule"]): string {
  if (s.kind === "daily") return `每天 ${s.time}`;
  if (s.kind === "weekdays") return `工作日 ${s.time}`;
  const days = [...(s.days || [])].sort().map((d) => DAYS[d]).join("、");
  return `每周${days || "—"} ${s.time}`;
}

/** "今天 09:00" / "明天 09:00" / the date for anything later. */
function nextText(value: string | null): string {
  if (!value) return "";
  const at = new Date(value);
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(at) - day(new Date())) / 86_400_000);
  const time = at.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return diff === 0 ? `今天 ${time}` : diff === 1 ? `明天 ${time}` : formatDate(value).replace(/:\d{2}$/, "");
}

function Editor({ initial, skills, onClose, onSaved }: { initial: Schedule | null; skills: Skill[]; onClose: () => void; onSaved: (s: Schedule) => void }) {
  const projects = useProjects();
  const [d, setD] = useState<Draft>(() => ({
    title: initial?.title || "",
    prompt: initial?.prompt || "",
    mode: initial?.mode || "work",
    skill: initial?.skill || null,
    project_id: initial?.project_id || null,
    kind: initial?.schedule.kind || "weekdays",
    time: initial?.schedule.time || "09:00",
    days: initial?.schedule.days || [0],
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const valid = d.title.trim() && d.prompt.trim() && /^\d{2}:\d{2}$/.test(d.time) && (d.kind !== "weekly" || d.days.length);

  async function save() {
    setBusy(true);
    setError("");
    const body = {
      title: d.title.trim(),
      prompt: d.prompt.trim(),
      mode: d.skill ? "work" : d.mode,
      skill: d.skill,
      project_id: d.project_id,
      schedule: { kind: d.kind, time: d.time, ...(d.kind === "weekly" ? { days: [...d.days].sort() } : {}) },
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    try {
      const saved = await api<Schedule>(initial ? `/v1/schedules/${initial.id}` : "/v1/schedules", {
        method: initial ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal title={initial ? "编辑定时任务" : "新建定时任务"} onClose={onClose} className="schedule-editor"
      footer={<>
        {error ? <span className="alert error grow-alert">{error}</span> : <span className="spacer" />}
        <button className="secondary" onClick={onClose} disabled={busy}>取消</button>
        <button onClick={save} disabled={!valid || busy}>{busy ? "保存中…" : "保存"}</button>
      </>}>
      <label className="field">
        <span>名称</span>
        <input type="text" value={d.title} maxLength={60} onChange={(e) => set({ title: e.target.value })} placeholder="每周销售周报" autoFocus />
      </label>
      <label className="field">
        <span>要做什么</span>
        <textarea rows={4} value={d.prompt} onChange={(e) => set({ prompt: e.target.value })} placeholder="汇总上周各区域销售数据，生成一页 PPT 周报" />
      </label>
      <div className="field">
        <span>时间</span>
        <div className="schedule-when">
          <div className="segmented" role="radiogroup" aria-label="频率">
            {([["daily", "每天"], ["weekdays", "工作日"], ["weekly", "每周"]] as [Kind, string][]).map(([k, label]) => (
              <button key={k} type="button" role="radio" aria-checked={d.kind === k} className={d.kind === k ? "on" : ""} onClick={() => set({ kind: k })}>{label}</button>
            ))}
          </div>
          <input type="time" value={d.time} onChange={(e) => set({ time: e.target.value })} aria-label="时刻" />
        </div>
        <div className="collapse" data-open={d.kind === "weekly"} inert={d.kind !== "weekly"}>
          <div>
            <div className="day-picker" role="group" aria-label="星期">
              {DAYS.map((label, i) => (
                <button key={i} type="button" aria-pressed={d.days.includes(i)} className={`day ${d.days.includes(i) ? "on" : ""}`}
                  onClick={() => set({ days: d.days.includes(i) ? d.days.filter((x) => x !== i) : [...d.days, i] })}>{label}</button>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="schedule-row">
        <label className="field">
          <span>模式</span>
          <select value={d.skill ? `skill:${d.skill}` : d.mode} onChange={(e) => {
            const v = e.target.value;
            if (v.startsWith("skill:")) set({ skill: v.slice(6), mode: "work" });
            else set({ skill: null, mode: v as "chat" | "work" });
          }}>
            <option value="chat">对话</option>
            <option value="work">工作</option>
            {skills.map((s) => <option key={s.name} value={`skill:${s.name}`}>{s.title}</option>)}
          </select>
        </label>
        <label className="field">
          <span>项目</span>
          <select value={d.project_id || ""} onChange={(e) => set({ project_id: e.target.value || null })}>
            <option value="">不放进项目</option>
            {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>
    </Modal>
  );
}

export default function SchedulesPage() {
  const [list, setList] = useState<Schedule[] | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => api<Schedule[]>("/v1/schedules").then(setList).catch((err) => setError(errorMessage(err))), []);
  useEffect(() => {
    load();
    api<Skill[]>("/v1/skills").then((all) => setSkills(all.filter((s) => s.enabled))).catch(() => undefined);
  }, [load]);
  // a task that is running now: its status settles on its own
  useEffect(() => {
    if (!list?.some((s) => s.last_status === "queued" || s.last_status === "running")) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [list, load]);

  async function act(s: Schedule, fn: () => Promise<unknown>) {
    setBusy(s.id);
    setError("");
    try {
      await fn();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const full = (list?.length || 0) >= LIMIT;
  return (
    <main className="page page-narrow schedules-page">
      <div className="library-head">
        <h1>定时任务</h1>
        <span className="spacer" />
        <button onClick={() => setEditing("new")} disabled={full || !list} title={full ? `最多 ${LIMIT} 个定时任务` : undefined}>
          <IconPlus size={16} /> 新建
        </button>
      </div>
      {error ? <div className="alert error library-alert">{error}</div> : null}
      {list === null ? <div className="pr-loading"><span /><span /><span /></div> : list.length === 0 ? (
        <div className="empty schedules-empty">
          <span className="project-mark"><IconClock size={20} /></span>
          <strong>还没有定时任务</strong>
          <button className="secondary" onClick={() => setEditing("new")}><IconPlus size={15} /> 新建定时任务</button>
        </div>
      ) : (
        <div className="card schedule-list">
          {list.map((s) => (
            <div key={s.id} className={`schedule ${s.enabled ? "" : "off"}`}>
              <button type="button" role="switch" aria-checked={s.enabled} aria-label={`${s.enabled ? "停用" : "启用"} ${s.title}`} className={`switch ${s.enabled ? "on" : ""}`}
                disabled={busy === s.id} onClick={() => act(s, () => api(`/v1/schedules/${s.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !s.enabled }) }))}>
                <span className="knob" />
              </button>
              <div className="grow">
                <div className="schedule-title">{s.title}</div>
                <div className="schedule-meta">
                  <span>{when(s.schedule)}</span>
                  {s.enabled && s.next_run_at ? <span>下次 {nextText(s.next_run_at)}</span> : null}
                  {s.last_conversation_id ? (
                    <Link href={`/c/${s.last_conversation_id}`} className={`schedule-last ${s.last_status || ""}`} title={s.last_error || undefined}>
                      {statusLabel(s.last_status || "")}
                    </Link>
                  ) : s.last_status === "failed" ? <span className="schedule-last failed" title={s.last_error || undefined}>{s.last_error || "失败"}</span> : null}
                </div>
              </div>
              <div className="row-actions">
                <button className="icon-btn" aria-label={`立即运行 ${s.title}`} title="立即运行" disabled={busy === s.id}
                  onClick={() => act(s, () => api(`/v1/schedules/${s.id}/run`, { method: "POST" }))}>
                  <IconSend size={16} />
                </button>
                <button className="icon-btn" aria-label={`编辑 ${s.title}`} title="编辑" onClick={() => setEditing(s)}>
                  <IconPencil size={16} />
                </button>
                <button className="icon-btn danger-hover" aria-label={`删除 ${s.title}`} title="删除" disabled={busy === s.id}
                  onClick={() => confirm(`删除定时任务「${s.title}」？`) && act(s, () => api(`/v1/schedules/${s.id}`, { method: "DELETE" }))}>
                  <IconTrash size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {editing ? (
        <Editor initial={editing === "new" ? null : editing} skills={skills} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }} />
      ) : null}
    </main>
  );
}
