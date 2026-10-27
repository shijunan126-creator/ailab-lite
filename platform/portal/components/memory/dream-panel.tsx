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
type Run = {
  id: string;
  status: "pending" | "running" | "applied" | "skipped" | "failed" | "reverted" | "evaluated";
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
  evaluated: "已评估",
};

/** The user never gets a notification for a dream; this is the audit surface they can open. */
export default function DreamPanel({ initial, onChanged }: { initial?: DreamSettings | null; onChanged?: () => void }) {
  const [settings, setSettings] = useState<DreamSettings | null>(initial || null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [metrics, setMetrics] = useState<{ runs: number; applied: number; changes: number; tokens: number } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmRevert, setConfirmRevert] = useState<Run | null>(null);

  const mounted = useRef(true);
  const loads = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    const [document, nextRuns, nextMetrics] = await Promise.all([
      api<{ dream: DreamSettings }>("/v1/memory", { signal: controller.signal }),
      api<Run[]>("/v1/memory/dreams", { signal: controller.signal }),
      api<{ runs: number; applied: number; changes: number; tokens: number }>("/v1/memory/dreams/metrics", { signal: controller.signal }),
    ]);
    if (!mounted.current || controller.signal.aborted) return nextRuns;
    setSettings(document.dream);
    setRuns(nextRuns);
    setMetrics(nextMetrics);
    return nextRuns;
  }, []);

  useEffect(() => {
    mounted.current = true;
    load().catch((e) => { if (mounted.current && !loads.current?.signal.aborted) setError(errorMessage(e)); });
    return () => { mounted.current = false; loads.current?.abort(); detailLoad.current?.abort(); };
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

  const detailLoad = useRef<AbortController | null>(null);
  async function inspect(run: Run) {
    detailLoad.current?.abort();
    const controller = new AbortController();
    detailLoad.current = controller;
    setError("");
    try {
      const value = await api<Detail>(`/v1/memory/dreams/${run.id}`, { signal: controller.signal });
      if (!controller.signal.aborted && mounted.current) { setDetail(value); setOpen(run.id); }
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
        if (!mounted.current) break;
        const next = await load();
        if (!next.some((run) => run.status === "pending" || run.status === "running")) { onChanged?.(); break; }
      }
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
          立即整理
        </button>
      </div>
      {confirmRevert ? <div role="alertdialog" aria-label="撤销记忆整理" className="alert">
        <span className="grow">撤销 {confirmRevert.local_date} 的整理？</span>
        <button className="secondary sm" onClick={() => setConfirmRevert(null)}>取消</button>
        <button className="sm" onClick={() => revert(confirmRevert)} disabled={busy}>确认撤销</button>
      </div> : null}
      {runs.length ? (
        <div className="dream-runs">
          {runs.map((run) => (
            <div key={run.id} className="dream-run">
              <button className="dream-run-head" onClick={() => (open === run.id ? (detailLoad.current?.abort(), setOpen(null), setDetail(null)) : inspect(run))}>
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
                    <button className="secondary sm" onClick={() => setConfirmRevert(detail)} disabled={busy}>撤销这次整理</button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
