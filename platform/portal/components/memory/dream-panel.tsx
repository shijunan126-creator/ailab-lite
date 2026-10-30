"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import Switch from "../switch";

type DreamSettings = { enabled: boolean; timezone: string | null; window_start: string; window_end: string };
type Change = {
  op: "add" | "invalidate" | "noop";
  id?: string | null;
  kind: "user" | "memory";
  content: string;
  confidence: number;
  reason: string;
};
type Outcome =
  | "added" | "updated" | "duplicate" | "deferred" | "unsupported" | "low_confidence" | "quote_mismatch" | "guard" | "tombstone" | "capacity";
type Candidate = { text: string; quote: string; outcome: Outcome; reason: string; related?: { id: string; text: string }[] };
type Pending = number | "200+";
type Report = {
  scope?: { project_id: string | null; runs: number; episodes: number; chars: number; trimmed_runs: number };
  counts?: Partial<Record<"candidates" | "added" | "updated" | "invalidated" | "duplicate" | "filtered" | "deferred", number>>;
  candidates?: Candidate[];
  pending_after?: Pending;
};
type Run = {
  id: string;
  status: "pending" | "running" | "applied" | "skipped" | "failed" | "reverted" | "evaluated";
  trigger: "manual" | "cron" | string;
  local_date: string;
  changes: Change[];
  journal: string;
  report: Report;
  error: string;
  created_at: string;
  finished_at: string | null;
};
type Status = { pending_runs: Pending; active: { id: string; status: string; trigger: string } | null; last_finished_at: string | null };

const OUTCOME: Record<Outcome, string> = {
  added: "新增",
  updated: "更新",
  duplicate: "重复",
  deferred: "延后",
  unsupported: "证据不足",
  low_confidence: "不确定",
  quote_mismatch: "引文不符",
  guard: "已过滤",
  tombstone: "曾删除",
  capacity: "容量已满",
};
const KEPT = new Set<Outcome>(["added", "updated"]);
const POLL_MS = 2000;
const POLL_FOR_MS = 15 * 60_000; // three manual batches of extraction and judgement fit well inside this

const SHOWN_DAYS = 3;

/** Batches of one local day, newest first: a busy night runs many small batches, the user reads it as one night. */
type Day = { date: string; runs: Run[] };
function byDay(runs: Run[]): Day[] {
  const days: Day[] = [];
  for (const run of runs) {
    const last = days[days.length - 1];
    if (last?.date === run.local_date) last.runs.push(run);
    else days.push({ date: run.local_date, runs: [run] });
  }
  return days;
}

function dayLabel(date: string): string {
  const local = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const now = new Date();
  if (date === local(now)) return "今天";
  if (date === local(new Date(now.getTime() - 86_400_000))) return "昨天";
  const [y, m, d] = date.split("-").map(Number);
  return `${y === now.getFullYear() ? "" : `${y}年`}${m}月${d}日`;
}

function summary(day: Day): { text: string; failed: boolean } {
  let added = 0, updated = 0, invalidated = 0, failed = 0, reverted = 0;
  for (const run of day.runs) {
    if (run.status === "failed") failed++;
    else if (run.status === "reverted") reverted++;
    else if (run.report?.counts) {
      added += run.report.counts.added || 0;
      updated += run.report.counts.updated || 0;
      invalidated += run.report.counts.invalidated || 0;
    } else
      for (const c of run.changes) {
        if (c.op === "add") added++;
        else if (c.op === "invalidate") invalidated++;
      }
  }
  const parts = [
    added ? `新增 ${added}` : "",
    updated ? `更新 ${updated}` : "",
    invalidated ? `失效 ${invalidated}` : "",
    reverted ? `撤销 ${reverted} 次` : "",
    failed ? `失败 ${failed} 次` : "",
  ].filter(Boolean);
  const live = day.runs.some((r) => r.status === "pending" || r.status === "running");
  return { text: parts.join(" · ") || (live ? "整理中…" : "没有新增"), failed: !!failed && !parts.slice(0, 3).some(Boolean) };
}

/** The user never gets a notification for a dream; this is where they start one and see, day by day, what it kept and why. */
export default function DreamPanel({ initial, onChanged }: { initial?: DreamSettings | null; onChanged?: () => void }) {
  const [settings, setSettings] = useState<DreamSettings | null>(initial || null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [open, setOpen] = useState<string | null>(null); // a day
  const [detail, setDetail] = useState<Run[] | null>(null);
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmRevert, setConfirmRevert] = useState<Run | null>(null);

  const mounted = useRef(true);
  const loads = useRef<AbortController | null>(null);
  const detailLoad = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    const [document, nextRuns, nextStatus] = await Promise.all([
      api<{ dream: DreamSettings }>("/v1/memory", { signal: controller.signal }),
      api<Run[]>("/v1/memory/dreams", { signal: controller.signal }),
      api<Status>("/v1/memory/dream/status", { signal: controller.signal }),
    ]);
    if (!mounted.current || controller.signal.aborted) return null;
    setSettings(document.dream);
    setRuns(nextRuns);
    setStatus(nextStatus);
    return nextStatus;
  }, []);

  useEffect(() => {
    mounted.current = true;
    load().catch((e) => { if (mounted.current && !loads.current?.signal.aborted) setError(errorMessage(e)); });
    return () => { mounted.current = false; loads.current?.abort(); detailLoad.current?.abort(); };
  }, [load]);

  // While a batch is queued or running (started here, in another tab or by the nightly job), follow it until it ends.
  const active = status?.active?.id || null;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const until = Date.now() + POLL_FOR_MS;
    const tick = async () => {
      if (stopped || Date.now() > until) return;
      try {
        const next = await load();
        if (stopped || !next) return;
        if (!next.active) { onChanged?.(); return; }
      } catch {
        // a missed poll is retried on the next tick
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, load, onChanged]);

  async function toggle(enabled: boolean) {
    if (!settings || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api<DreamSettings>("/v1/memory/dream", {
        method: "PATCH",
        body: JSON.stringify({ ...settings, enabled }),
      });
      setSettings(saved);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function inspect(day: Day) {
    detailLoad.current?.abort();
    const controller = new AbortController();
    detailLoad.current = controller;
    setError("");
    setOpen(day.date);
    setDetail(null);
    try {
      const value = await Promise.all(day.runs.map((run) => api<Run>(`/v1/memory/dreams/${run.id}`, { signal: controller.signal })));
      if (!controller.signal.aborted && mounted.current) setDetail(value);
    } catch (e) {
      if (!controller.signal.aborted) setError(errorMessage(e));
    }
  }

  function collapse() {
    detailLoad.current?.abort();
    setOpen(null);
    setDetail(null);
  }

  async function runNow() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const started = await api<{ id: string | null; status: string; reused: boolean }>("/v1/memory/dream/run", { method: "POST" });
      if (started.status === "nothing") setNotice("没有新的对话需要整理");
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function revert(run: Run) {
    setConfirmRevert(null);
    setBusy(true);
    setError("");
    try {
      await api(`/v1/memory/dreams/${run.id}/revert`, { method: "POST" });
      setOpen(null);
      setDetail(null);
      await load();
      onChanged?.();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const pending = status?.pending_runs ?? 0;
  const days = byDay(runs);
  // only the newest change can be undone: a revert restores the memory as it was before that batch
  const latest = runs.find((r) => r.changes.some((c) => c.op !== "noop") && r.status !== "failed")?.id || null;
  const working = !!status?.active;
  return (
    <section className="card dream-panel">
      <div className="skill-row">
        <div className="grow">
          <div className="skill-title">夜间自动整理</div>
          {settings ? (
            <div className="skill-desc">
              {settings.window_start}–{settings.window_end}
              {settings.timezone ? ` · ${settings.timezone}` : ""}
            </div>
          ) : null}
        </div>
        <Switch on={!!settings?.enabled} label="夜间自动整理" disabled={busy || !settings} onChange={toggle} />
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      <div className="dream-actions" aria-live="polite">
        <span className={`dream-state grow ${working ? "live" : ""}`}>
          {working
            ? status?.active?.status === "running" ? "正在整理…" : "等待整理…"
            : pending ? `${pending} 段新对话待整理` : notice || "没有待整理的新对话"}
        </span>
        <button className="secondary sm" onClick={runNow} disabled={busy || working || !settings || !pending}>
          立即整理
        </button>
      </div>
      {confirmRevert ? <div role="alertdialog" aria-label="撤销记忆整理" className="alert">
        <span className="grow">撤销最近一次整理？它新增的记忆会删除，失效的会恢复。</span>
        <button className="secondary sm" onClick={() => setConfirmRevert(null)}>取消</button>
        <button className="sm" onClick={() => revert(confirmRevert)} disabled={busy}>确认撤销</button>
      </div> : null}
      {days.length ? (
        <div className="dream-runs">
          {(all ? days : days.slice(0, SHOWN_DAYS)).map((day) => {
            const sum = summary(day);
            return (
              <div key={day.date} className="dream-run">
                <button className="dream-run-head" aria-expanded={open === day.date} onClick={() => (open === day.date ? collapse() : inspect(day))}>
                  <span>{dayLabel(day.date)}</span>
                  <span className="spacer" />
                  <span className={`dream-sum ${sum.failed ? "failed" : ""}`}>{sum.text}</span>
                </button>
                {open === day.date && detail ? (
                  <DayDetail runs={detail} latest={latest} busy={busy} onRevert={(run) => setConfirmRevert(run)} />
                ) : null}
              </div>
            );
          })}
          {days.length > SHOWN_DAYS ? (
            <button className="link dream-more" onClick={() => setAll(!all)}>{all ? "收起" : "更早"}</button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DayDetail({ runs, latest, busy, onRevert }: { runs: Run[]; latest: string | null; busy: boolean; onRevert: (run: Run) => void }) {
  const live = runs.filter((r) => r.status !== "reverted" && r.status !== "failed");
  const total = (key: "runs" | "chars") => live.reduce((n, r) => n + (r.report?.scope?.[key] || 0), 0);
  const after = runs[0]?.report?.pending_after;
  const facts = [
    total("runs") ? `${total("runs")} 段对话` : "",
    total("chars") ? `${total("chars").toLocaleString()} 字` : "",
    after ? `还有 ${after} 段待整理` : "",
  ].filter(Boolean);
  const candidates = live.flatMap((r) => r.report?.candidates || []);
  candidates.sort((a, b) => Number(KEPT.has(b.outcome)) - Number(KEPT.has(a.outcome)));
  const invalidated = live.flatMap((r) => r.changes.filter((c) => c.op === "invalidate"));
  const legacy = live.filter((r) => !r.report?.counts).flatMap((r) => r.changes);
  const errors = [...new Set(runs.filter((r) => r.status === "failed").map((r) => r.error || "整理失败"))];
  const reverted = runs.filter((r) => r.status === "reverted").length;
  const undo = runs.find((r) => r.id === latest && r.status === "applied");
  return (
    <div className="dream-detail">
      {facts.length ? <div className="hint">{facts.join(" · ")}</div> : null}
      {errors.map((e) => <div key={e} className="alert error">{e}</div>)}
      {live.length && !candidates.length && !invalidated.length && !legacy.length ? <div className="hint">这些对话没有可长期保存的内容</div> : null}
      {candidates.map((c, i) => (
        <div key={i} className={`dream-cand ${KEPT.has(c.outcome) ? "kept" : ""}`}>
          <span className={`dream-op ${c.outcome}`}>{OUTCOME[c.outcome] || c.outcome}</span>
          <div className="grow">
            <div>{c.text || "（内容已删除）"}</div>
            {c.quote ? <q className="dream-quote">{c.quote}</q> : null}
            {c.related?.length ? (
              <div className="dream-related">{c.related.map((r) => <div key={r.id}>{r.text}</div>)}</div>
            ) : null}
          </div>
          {c.reason && !KEPT.has(c.outcome) ? <span className="hint dream-reason">{c.reason}</span> : null}
        </div>
      ))}
      {invalidated.map((c, i) => (
        <div key={`x${i}`} className="dream-cand">
          <span className="dream-op invalidate">失效</span>
          <div className="grow">{c.content}</div>
          {c.reason ? <span className="hint dream-reason">{c.reason}</span> : null}
        </div>
      ))}
      {legacy.map((c, i) => (
        <div key={`c${i}`} className="dream-cand">
          <span className="dream-op">{c.op === "add" ? "新增" : c.op === "invalidate" ? "失效" : "无变更"}</span>
          <span className="grow">{c.content || c.reason}</span>
        </div>
      ))}
      {reverted ? <div className="hint">已撤销 {reverted} 次整理</div> : null}
      {undo ? <button className="secondary sm dream-revert" onClick={() => onRevert(undo)} disabled={busy}>撤销最近一次整理</button> : null}
    </div>
  );
}
