"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { IconClock, IconPencil, IconPlus, IconSend, IconTrash } from "../../../components/icons";
import RowMenu from "../../../components/row-menu";
import Switch from "../../../components/switch";
import Modal from "../../../components/library/modal";
import { api, errorMessage, Schedule, Skill } from "../../../lib/api";
import { formatDate, statusLabel } from "../../../lib/format";
import { useProjects } from "../../../lib/projects";
import { confirmDialog } from "../../../lib/dialog";
import Select, { TimeSelect } from "../../../components/select";

const LIMIT = 5; // the API enforces the same
const DAYS = ["一", "二", "三", "四", "五", "六", "日"];
type Kind = Schedule["schedule"]["kind"];
type Draft = Pick<Schedule, "title" | "prompt" | "mode" | "skill" | "project_id" | "ends_on"> & { kind: Kind; time: string; days: number[] };
// the platform also pauses a task whose results go unread or that keeps failing
const PAUSED: Record<NonNullable<Schedule["paused_reason"]>, string> = {
  ended: "已到结束日期",
  unread: "结果一直没人看，已暂停",
  failures: "连续失败，已暂停",
};

/** YYYY-MM-DD of the local day `days` from today. */
function fromToday(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const dayText = (value: string) => {
  const [y, m, d] = value.split("-").map(Number);
  return `${y === new Date().getFullYear() ? "" : `${y}年`}${m}月${d}日`;
};

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
    ends_on: initial?.ends_on || null,
  }));
  const ends = [
    { value: "", label: "不结束" },
    ...(d.ends_on ? [{ value: d.ends_on, label: dayText(d.ends_on) }] : []),
    { value: fromToday(7), label: "一周后" },
    { value: fromToday(30), label: "一个月后" },
    { value: fromToday(91), label: "三个月后" },
    { value: fromToday(182), label: "半年后" },
  ].filter((o, i, all) => all.findIndex((x) => x.value === o.value) === i);
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
      mode: d.mode,
      skill: d.skill,
      project_id: d.project_id,
      schedule: { kind: d.kind, time: d.time, ...(d.kind === "weekly" ? { days: [...d.days].sort() } : {}) },
      ends_on: d.ends_on,
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
          <TimeSelect value={d.time} onChange={(time) => set({ time })} aria-label="时刻" />
          <Select aria-label="结束" value={d.ends_on || ""} options={ends} onChange={(v) => set({ ends_on: v || null })} />
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
          <Select
            aria-label="模式"
            value={d.mode}
            options={[{ value: "chat", label: "对话" }, { value: "work", label: "工作" }]}
            onChange={(v) => set({ mode: v as "chat" | "work" })}
          />
        </label>
        <label className="field">
          <span>技能</span>
          <Select
            aria-label="技能"
            value={d.skill || ""}
            options={[{ value: "", label: "不指定" }, ...skills.map((s) => ({ value: s.name, label: s.title }))]}
            onChange={(v) => set({ skill: v || null })}
          />
        </label>
        <label className="field">
          <span>项目</span>
          <Select
            aria-label="项目"
            value={d.project_id || ""}
            options={[{ value: "", label: "不放进项目" }, ...(projects || []).map((p) => ({ value: p.id, label: p.name }))]}
            onChange={(v) => set({ project_id: v || null })}
          />
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
    api<Skill[]>("/v1/skills").then((all) => setSkills(all.filter((s) => (s.enabled || s.kind === "base") && !s.implicit))).catch(() => undefined);
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
    <main className="page mpage">
      <div className="mpage-head">
        <h1>定时任务</h1>
        {list?.length ? <span className="hint">{list.length} / {LIMIT}</span> : null}
        <button className="sm" onClick={() => setEditing("new")} disabled={full || !list} title={full ? `最多 ${LIMIT} 个定时任务` : undefined}>
          <IconPlus size={15} /> 新建
        </button>
      </div>
      {error ? <div className="alert error" style={{ marginBottom: 12 }}>{error}</div> : null}
      <div className="mlist">
        {list === null ? (
          <div className="mlist-empty">加载中…</div>
        ) : list.length === 0 ? (
          <div className="mlist-empty">还没有定时任务</div>
        ) : (
          list.map((s) => (
            <div key={s.id} className={`mrow ${s.enabled ? "" : "off"}`}>
              <span className="mrow-icon"><IconClock size={17} /></span>
              <button className="mrow-main" onClick={() => setEditing(s)} aria-label={`编辑 ${s.title}`}>
                <span className="mrow-title"><span>{s.title}</span></span>
                <span className="mrow-desc">
                  {[
                    when(s.schedule),
                    s.paused_reason && !s.enabled ? PAUSED[s.paused_reason] : s.enabled && s.next_run_at ? `下次 ${nextText(s.next_run_at)}` : "",
                    s.ends_on && s.enabled ? `${dayText(s.ends_on)}结束` : "",
                  ].filter(Boolean).join(" · ")}
                </span>
              </button>
              <div className="mrow-trail">
                {s.last_conversation_id ? (
                  <Link href={`/c/${s.last_conversation_id}`} className={`state ${s.last_status === "failed" ? "failed" : ""}`} title={s.last_error || undefined}>
                    {statusLabel(s.last_status || "")}
                  </Link>
                ) : s.last_status === "failed" ? <span className="state failed" title={s.last_error || undefined}>失败</span> : null}
                <RowMenu
                  label={s.title}
                  disabled={busy === s.id}
                  actions={[
                    { label: "立即运行", icon: <IconSend size={15} />, onSelect: () => act(s, () => api(`/v1/schedules/${s.id}/run`, { method: "POST" })) },
                    { label: "编辑", icon: <IconPencil size={15} />, onSelect: () => setEditing(s) },
                    "sep",
                    {
                      label: "删除", icon: <IconTrash size={15} />, danger: true,
                      onSelect: async () => (await confirmDialog({ title: `删除定时任务「${s.title}」？`, action: "删除", danger: true })) && act(s, () => api(`/v1/schedules/${s.id}`, { method: "DELETE" })),
                    },
                  ]}
                />
                <Switch
                  on={s.enabled}
                  label={`${s.enabled ? "停用" : "启用"} ${s.title}`}
                  disabled={busy === s.id}
                  onChange={() => act(s, () => api(`/v1/schedules/${s.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !s.enabled }) }))}
                />
              </div>
            </div>
          ))
        )}
      </div>
      {editing ? (
        <Editor initial={editing === "new" ? null : editing} skills={skills} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }} />
      ) : null}
    </main>
  );
}
