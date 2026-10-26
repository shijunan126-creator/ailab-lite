"use client";

import { useCallback, useEffect, useState } from "react";
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
type Run = {
  id: string;
  status: "pending" | "running" | "applied" | "skipped" | "failed" | "reverted";
  local_date: string;
  input_run_ids: string[];
  changes: Change[];
  journal: string;
  token_usage: { prompt_tokens?: number; completion_tokens?: number };
  error: string;
  created_at: string;
  finished_at: string | null;
};
type Detail = Run & { before: Entry[]; after: Entry[] };
type Entry = { id: string; kind: "user" | "memory"; content: string; confidence?: number; superseded_by?: string | null };

const STATUS: Record<string, string> = {
  pending: "等待整理",
  running: "整理中",
  applied: "已整理",
  skipped: "本轮跳过",
  failed: "整理失败",
  reverted: "已撤销",
};

/** The user never gets a notification for a dream; this is the audit surface they can open. */
export default function DreamPanel({ initial }: { initial?: DreamSettings | null }) {
  const [settings, setSettings] = useState<DreamSettings | null>(initial || null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [metrics, setMetrics] = useState<{ runs: number; applied: number; changes: number; tokens: number } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [document, nextRuns, nextMetrics] = await Promise.all([
      api<{ dream: DreamSettings }>("/v1/memory"),
      api<Run[]>("/v1/memory/dreams"),
      api<{ runs: number; applied: number; changes: number; tokens: number }>("/v1/memory/dreams/metrics"),
    ]);
    setSettings(document.dream);
    setRuns(nextRuns);
    setMetrics(nextMetrics);
    return nextRuns;
  }, []);

  useEffect(() => {
    load().catch((e) => setError(errorMessage(e)));
  }, [load]);

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
    setError("");
    try {
      setDetail(await api<Detail>(`/v1/memory/dreams/${run.id}`));
      setOpen(run.id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function runNow() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api("/v1/memory/dream/run", { method: "POST" });
      for (let i = 0; i < 15; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const next = await load();
        if (!next.some((run) => run.status === "pending" || run.status === "running")) break;
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function revert(run: Run) {
    if (!window.confirm(`撤销 ${run.local_date} 的记忆整理？恢复到整理前的内容。`)) return;
    setBusy(true);
    setError("");
    try {
      await api(`/v1/memory/dreams/${run.id}/revert`, { method: "POST" });
      setOpen(null);
      setDetail(null);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card dream-panel">
      <div className="skill-row">
        <div className="grow">
          <div className="skill-title">夜间自动整理</div>
          <div className="skill-desc">在你不用的时候整理重复和过时内容，不打断对话；可随时撤销。</div>
        </div>
        <Switch on={!!settings?.enabled} label="夜间自动整理" disabled={busy || !settings} onChange={toggle} />
      </div>
      {settings ? (
        <div className="hint dream-window">
          低峰时段 {settings.window_start}–{settings.window_end}
          {settings.timezone ? ` · ${settings.timezone}` : ""}
        </div>
      ) : null}
      {error ? <div className="alert error">{error}</div> : null}
      <div className="dream-actions">
        <span className="hint grow">
          {metrics && metrics.runs > 0
            ? `已整理 ${metrics.applied} 次 · 变更 ${metrics.changes} 条 · 使用 ${metrics.tokens.toLocaleString()} tokens`
            : "还没有整理记录"}
        </span>
        <button className="secondary sm" onClick={runNow} disabled={busy || !settings?.enabled}>
          立即整理一次
        </button>
      </div>
      {runs.length ? (
        <div className="dream-runs">
          {runs.map((run) => (
            <div key={run.id} className="dream-run">
              <button className="dream-run-head" onClick={() => (open === run.id ? (setOpen(null), setDetail(null)) : inspect(run))}>
                <span>{run.local_date}</span>
                <span className={`dream-status ${run.status}`}>{STATUS[run.status] || run.status}</span>
                <span className="spacer" />
                <span className="hint">{run.changes.length ? `${run.changes.length} 条变更` : run.journal || "无变更"}</span>
              </button>
              {open === run.id && detail ? (
                <div className="dream-detail">
                  {detail.changes.length ? detail.changes.map((change, i) => (
                    <div key={i} className="dream-change">
                      <span className="dream-op">{change.op === "add" ? "新增" : change.op === "invalidate" ? "失效" : "无变更"}</span>
                      <span className="grow">{change.content || change.reason}</span>
                      <span className="hint">{Math.round(change.confidence * 100)}%</span>
                    </div>
                  )) : <div className="hint">这次没有需要保留的新内容。</div>}
                  {detail.journal ? <p className="hint dream-journal">{detail.journal}</p> : null}
                  {detail.status === "applied" ? (
                    <button className="secondary sm" onClick={() => revert(detail)} disabled={busy}>撤销这次整理</button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : <div className="hint dream-empty">还没有夜间整理记录。</div>}
    </section>
  );
}
