"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { IconDownload, IconFile } from "../icons";
import { EventItem, Run, api, downloadUrl, errorMessage, streamRun } from "../../lib/api";
import { formatBytes, formatDate, isTerminal, statusLabel } from "../../lib/format";

type Described = { label: string; detail: string; tone?: "done" | "bad" | "tool_call" | "tool_result" };

function describe(event: EventItem): Described {
  const p = event.payload || {};
  switch (event.type) {
    case "run.created":
      return { label: "任务已创建", detail: `status=${p.status} model=${p.model}` };
    case "run.status":
      return { label: "状态变更", detail: `status=${p.status}` };
    case "run.completed":
      return { label: "任务完成", detail: p.summary || "", tone: "done" };
    case "run.failed":
      return { label: "任务失败", detail: p.error || "失败", tone: "bad" };
    case "run.canceled":
      return { label: "任务已取消", detail: "", tone: "bad" };
    case "agent.error":
      return { label: "Agent 出错", detail: p.error || "失败", tone: "bad" };
    case "agent.started":
      return { label: "Agent 开始工作", detail: `model=${p.model}` };
    case "agent.message":
      return { label: "模型回复", detail: p.content || "" };
    case "agent.tool_call":
      return { label: `调用工具 ${p.name}`, detail: JSON.stringify(p.arguments || {}).slice(0, 240), tone: "tool_call" };
    case "agent.tool_result": {
      const parts = [p.ok ? "ok" : "failed"];
      if (p.exit_code !== undefined && p.exit_code !== null) parts.push(`exit=${p.exit_code}`);
      if (p.error) parts.push(p.error);
      if (p.stdout) parts.push(String(p.stdout).slice(0, 300));
      return { label: p.ok ? "工具返回" : "工具失败", detail: parts.join(" · "), tone: p.ok ? "tool_result" : "bad" };
    }
    case "artifact.created":
      return { label: "生成文件", detail: `${p.filename} · ${formatBytes(p.size || 0)}`, tone: "done" };
    case "agent.sandbox":
      return { label: "运行环境", detail: `${p.provider || ""} ${p.lease_id || ""}`.trim() };
    case "sandbox.leased":
      return { label: "运行环境已就绪", detail: `${p.provider} · ${p.lease_id}` };
    case "sandbox.released":
      return { label: "运行环境已释放", detail: p.reason || "released" };
    default:
      return { label: event.type, detail: JSON.stringify(p).slice(0, 300) };
  }
}

export default function RunDetails({ id }: { id: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState(true);

  // Several events can each trigger a reload while earlier ones are still in flight. Only the most recently
  // issued reload may update the page: otherwise a slow "running" response arriving after the "succeeded"
  // one would put the stale status back, and nothing would refresh it once the stream has ended.
  const loadSeq = useRef(0);
  const request = useRef<AbortController | null>(null);
  const loadRun = useCallback(async () => {
    const mine = ++loadSeq.current;
    try {
      const fresh = await api<Run>(`/v1/runs/${id}`, { signal: request.current?.signal });
      if (mine === loadSeq.current) setRun(fresh);
    } catch (err: any) {
      if (mine === loadSeq.current && !request.current?.signal.aborted) setError(errorMessage(err));
    }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const controller = new AbortController();
    request.current = controller;
    loadRun();
    streamRun(
      id,
      (event) => {
        if (cancelled) return;
        setEvents((prev) => (prev.some((e) => e.seq === event.seq) ? prev : [...prev, event]));
        if (["run.status", "artifact.created", "run.completed", "run.failed", "run.canceled"].includes(event.type)) {
          loadRun();
        }
      },
      controller.signal,
      () => setStreaming(false)
    ).catch((err) => {
      if (!cancelled) {
        setError(errorMessage(err));
        setStreaming(false);
      }
    });
    return () => {
      cancelled = true;
      ++loadSeq.current;
      controller.abort();
    };
  }, [id, loadRun]);

  async function cancel() {
    try {
      await api(`/v1/runs/${id}/cancel`, { method: "POST" });
      await loadRun();
    } catch (err: any) {
      setError(errorMessage(err));
    }
  }

  const terminal = run ? isTerminal(run.status) : false;

  return (
    <div className="run-details">
      <div className="page-head page-head-row">
        <div>
          <h1>{run?.title || "任务详情"}</h1>
          <div className="chips">
            <span className="chip">model: {run?.model || "-"}</span>
            {run?.usage ? (
              <span className="chip">
                tokens: {run.usage.prompt_tokens || 0} + {run.usage.completion_tokens || 0}
              </span>
            ) : null}
            {run ? <span className="chip">创建于 {formatDate(run.created_at)}</span> : null}
            <span className="chip mono">{id}</span>
          </div>
        </div>
        {run ? <span className={`status ${run.status}`}>{statusLabel(run.status)}</span> : null}
        {run && !terminal ? (
          <button className="danger" onClick={cancel}>
            取消任务
          </button>
        ) : null}
      </div>

      <div className="stack">
        <section className="card card-pad">
          <div className="card-title">请求</div>
          <div className="prose quote">{run?.prompt || "…"}</div>
        </section>

        {run?.summary ? (
          <section className="card card-pad">
            <div className="card-title">结果</div>
            <div className="prose">{run.summary}</div>
          </section>
        ) : null}

        {run?.error ? (
          <section className="card card-pad">
            <div className="card-title" style={{ color: "var(--destructive)" }}>错误</div>
            <div className="alert error">{run.error}</div>
          </section>
        ) : null}

        {run?.artifacts && run.artifacts.length > 0 ? (
          <section className="card card-pad">
            <div className="card-title">产出</div>
            <div className="artifacts">
              {run.artifacts.map((artifact) => (
                <div className="artifact" key={artifact.id}>
                  <span className="icon">
                    <IconFile />
                  </span>
                  <div className="grow">
                    <div className="name" title={artifact.filename}>{artifact.filename}</div>
                    <div className="size">{artifact.deleted ? "已删除" : `${formatBytes(artifact.size)} · ${artifact.content_type}`}</div>
                  </div>
                  {artifact.deleted ? null : (
                    <a className="btn secondary sm" href={downloadUrl(artifact.download_url)} target="_blank" rel="noreferrer" aria-label={`下载 ${artifact.filename}`}>
                      <IconDownload size={15} /> 下载
                    </a>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <section className="card card-pad">
          <div className="card-title">
            执行时间线
            <span className="spacer" />
            <span className={`live ${streaming ? "on" : ""}`}>{streaming ? "实时" : "已结束"}</span>
            <span className="hint">{events.length} events</span>
          </div>
          {events.length === 0 ? (
            <div className="hint">等待事件…</div>
          ) : (
            <ol className="timeline">
              {events.map((event) => {
                const d = describe(event);
                return (
                  <li key={event.seq} className={`event ${d.tone || ""}`}>
                    <div className="event-head">
                      <span className="event-label">{d.label}</span>
                      <span className="event-type">
                        #{event.seq} {event.type}
                      </span>
                    </div>
                    {d.detail ? <div className="event-detail">{d.detail}</div> : null}
                    {event.type === "agent.tool_result" && (event.payload?.stdout || event.payload?.stderr) ? (
                      <details>
                        <summary>查看输出</summary>
                        {event.payload?.stdout ? <pre className="log">{event.payload.stdout}</pre> : null}
                        {event.payload?.stderr ? <pre className="log">{event.payload.stderr}</pre> : null}
                      </details>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
          {error ? <div className="alert error" style={{ marginTop: 12 }}>{error}</div> : null}
        </section>
      </div>
    </div>
  );
}
