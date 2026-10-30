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

const STATUS: Record<string, string> = {
  pending: "排队中",
  running: "整理中",
  applied: "已整理",
  skipped: "已跳过",
  failed: "失败",
  reverted: "已撤销",
  evaluated: "已评估",
};
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

function summary(run: Run): string {
  if (run.status === "failed") return run.error || "整理失败";
  if (run.status === "pending" || run.status === "running") return "";
  const c = run.report?.counts;
  if (!c) return run.changes.length ? `${run.changes.length} 条变更` : "无变更";
  if (!c.candidates) return "没有可长期保存的内容";
  const parts = [
    c.added ? `新增 ${c.added}` : "",
    c.updated ? `更新 ${c.updated}` : "",
    c.invalidated ? `失效 ${c.invalidated}` : "",
    c.duplicate ? `重复 ${c.duplicate}` : "",
    c.filtered ? `过滤 ${c.filtered}` : "",
    c.deferred ? `延后 ${c.deferred}` : "",
  ].filter(Boolean);
  return parts.join(" · ") || "无变更";
}

/** The user never gets a notification for a dream; this is where they start one and see what each batch did and why. */
export default function DreamPanel({ initial, onChanged }: { initial?: DreamSettings | null; onChanged?: () => void }) {
  const [settings, setSettings] = useState<DreamSettings | null>(initial || null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Run | null>(null);
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

  async function inspect(run: Run) {
    detailLoad.current?.abort();
    const controller = new AbortController();
    detailLoad.current = controller;
    setError("");
    setOpen(run.id);
    setDetail(null);
    try {
      const value = await api<Run>(`/v1/memory/dreams/${run.id}`, { signal: controller.signal });
      if (!controller.signal.aborted && mounted.current) setDetail(value);
    } catch (e) {
      if (!controller.signal.aborted) setError(errorMessage(e));
    }
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
        <button className="secondary sm" onClick={runNow} disabled={busy || working || !settings?.enabled || !pending}>
          立即整理
        </button>
      </div>
      {confirmRevert ? <div role="alertdialog" aria-label="撤销记忆整理" className="alert">
        <span className="grow">撤销 {confirmRevert.local_date} 的这次整理？</span>
        <button className="secondary sm" onClick={() => setConfirmRevert(null)}>取消</button>
        <button className="sm" onClick={() => revert(confirmRevert)} disabled={busy}>确认撤销</button>
      </div> : null}
      {runs.length ? (
        <div className="dream-runs">
          {runs.map((run) => (
            <div key={run.id} className="dream-run">
              <button
                className="dream-run-head"
                aria-expanded={open === run.id}
                onClick={() => (open === run.id ? (detailLoad.current?.abort(), setOpen(null), setDetail(null)) : inspect(run))}
              >
                <span>{run.local_date}</span>
                <span className={`dream-status ${run.status}`}>{run.trigger === "manual" ? "手动" : "自动"} · {STATUS[run.status] || run.status}</span>
                <span className="spacer" />
                <span className={`dream-sum ${run.status === "failed" ? "failed" : ""}`}>{summary(run)}</span>
              </button>
              {open === run.id && detail?.id === run.id ? <RunDetail run={detail} busy={busy} onRevert={() => setConfirmRevert(detail)} /> : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function RunDetail({ run, busy, onRevert }: { run: Run; busy: boolean; onRevert: () => void }) {
  const scope = run.report?.scope;
  const candidates = run.report?.candidates || [];
  const after = run.report?.pending_after;
  const facts = [
    scope ? `${scope.runs} 段对话` : "",
    scope?.chars ? `${scope.chars.toLocaleString()} 字` : "",
    scope?.trimmed_runs ? `${scope.trimmed_runs} 段较长，余下部分留到下一批` : "",
    after ? `还有 ${after} 段待整理` : "",
  ].filter(Boolean);
  const invalidated = run.changes.filter((c) => c.op === "invalidate");
  return (
    <div className="dream-detail">
      {facts.length ? <div className="hint">{facts.join(" · ")}</div> : null}
      {run.status === "failed" ? <div className="alert error">{run.error || "整理失败"}</div> : null}
      {run.status !== "failed" && run.report?.counts && !candidates.length ? <div className="hint">这批对话没有可长期保存的内容</div> : null}
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
      {!run.report?.counts && run.changes.length ? run.changes.map((c, i) => (
        <div key={`c${i}`} className="dream-cand">
          <span className="dream-op">{c.op === "add" ? "新增" : c.op === "invalidate" ? "失效" : "无变更"}</span>
          <span className="grow">{c.content || c.reason}</span>
        </div>
      )) : null}
      {run.status === "applied" && run.changes.some((c) => c.op !== "noop") ? (
        <button className="secondary sm dream-revert" onClick={onRevert} disabled={busy}>撤销这次整理</button>
      ) : null}
    </div>
  );
}
