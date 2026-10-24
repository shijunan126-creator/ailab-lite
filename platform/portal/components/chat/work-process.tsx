"use client";

import { useState } from "react";
import type { Run } from "../../lib/api";
import { type Item, type PlanStep, workHeading } from "../../lib/work-process";
import Markdown from "../markdown";

const MARK: Record<PlanStep["status"], string> = { pending: "pending", running: "running", done: "ok", skipped: "note", failed: "failed" };

export default function WorkProcess({ run, items, loading, error, more, onLoad, renderAsk }: {
  run: Run;
  items: Item[] | null | undefined;
  loading: boolean;
  error: string;
  more: boolean;
  onLoad: () => void;
  renderAsk: (item: Extract<Item, { kind: "ask" }>) => React.ReactNode;
}) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const active = ["queued", "running", "waiting_user", "interrupted"].includes(run.status);
  const open = expanded ?? active;
  const thinking = run.options?.mode === "chat" ? "思考" : "梳理任务";
  return <section className={`work-process steps ${open ? "open" : ""}`} aria-label={run.options?.mode === "chat" ? "思考过程" : "工作过程"}>
    <button className="steps-head" aria-expanded={open} onClick={() => {
      setExpanded(!open);
      if (!open && !items && !loading) onLoad();
    }}>
      <span className={`dot ${run.status === "running" ? "spin" : ""}`} />
      <span>{workHeading(run)}</span><span className="chev">{open ? "▾" : "▸"}</span>
    </button>
    {open ? <div className="work-timeline">
      {items?.map((item, i) => <div key={i} className={`work-entry process-${item.kind}`}>
        {item.kind === "text" ? (item.text.trim() ? <Markdown text={item.text} /> : null)
          : item.kind === "thinking" ? <div className="step note"><span className="step-mark" /><span>{thinking}</span></div>
          : item.kind === "ask" ? renderAsk(item)
          : item.kind === "plan" ? <ol className="steps-list" aria-label="计划">{item.steps.map((step) => <li key={step.id} className={`step ${MARK[step.status]}`}>
            <span className="step-mark" /><div className="step-body"><span className="step-label">{step.title}</span></div>
          </li>)}</ol>
          : <ol className="steps-list">{item.steps.map((step, j) => <li key={j} className={`step ${step.status === "running" && !active ? "note" : step.status}`}>
            <span className="step-mark" /><div className="step-body"><span className="step-label">{step.name === "thinking" ? thinking : step.label}</span>
              {step.name !== "thinking" && step.detail ? <span className="step-detail">{step.detail}</span> : null}
              {step.name !== "thinking" && step.output ? <details><summary>输出</summary><pre className="log">{step.output}</pre></details> : null}
            </div>
          </li>)}</ol>}
      </div>)}
      {loading ? <div className="hint">正在加载…</div> : null}
      {error ? <button className="link" onClick={onLoad}>加载失败，重试</button> : null}
      {more && !loading ? <button className="link" onClick={onLoad}>加载更多过程</button> : null}
    </div> : null}
  </section>;
}
