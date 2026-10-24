"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { Run } from "../../lib/api";
import { type Item, type PlanStep, type Step, siteOf, thoughtOutline, workHeading } from "../../lib/work-process";
import { IconBook, IconBulb, IconCheck, IconChevronDown, IconFile, IconFilePen, IconGlobe, IconSearch, IconSparkles, IconTerminal, IconX, IconCornerDownRight } from "../icons";
import Markdown from "../markdown";

const MARK: Record<PlanStep["status"], string> = { pending: "pending", running: "running", done: "ok", skipped: "note", failed: "failed" };

function StepIcon({ name }: { name: string }) {
  if (name === "web_search" || name === "search_images" || name === "search_library") return <IconSearch size={14} />;
  if (name === "read_url" || name === "download_url") return <IconGlobe size={14} />;
  if (name === "exec" || name === "fetch_packages" || name === "fetch_repo") return <IconTerminal size={14} />;
  if (name === "write_file" || name === "save_skill") return <IconFilePen size={14} />;
  if (name === "read_library_file" || name === "copy_library_file") return <IconBook size={14} />;
  if (name === "load_skill") return <IconSparkles size={14} />;
  return <IconFile size={14} />;
}

function Status({ status }: { status: Step["status"] }) {
  if (status === "running") return <span className="pr-spin" aria-label="进行中" />;
  if (status === "failed") return <span className="pr-status failed" aria-label="失败"><IconX size={12} /></span>;
  if (status === "ok") return <span className="pr-status ok" aria-label="完成"><IconCheck size={12} /></span>;
  return null;
}

/** One reasoning block as an outline: a row per part, the full text of a part only when it is opened. */
function Thinking({ item, label, live }: { item: Extract<Item, { kind: "thinking" }>; label: string; live: boolean }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const parts = thoughtOutline(item.text);
  const thinkingNow = live && !item.done;
  if (!parts.length)
    return <div className={`pr-row pr-think ${thinkingNow ? "now" : ""}`}>
      <span className="pr-icon"><IconBulb size={14} /></span>
      <span className={`pr-label ${thinkingNow ? "shimmer" : ""}`}>{thinkingNow ? `正在${label}` : label}</span>
      {item.seconds >= 1 ? <span className="pr-time">{Math.round(item.seconds)} 秒</span> : null}
    </div>;
  return <>{parts.map((part, j) => {
    const last = j === parts.length - 1;
    const expandable = !!part.body && part.body !== part.title;
    const expanded = open.has(j);
    const toggle = () => setOpen((s) => { const n = new Set(s); if (n.has(j)) n.delete(j); else n.add(j); return n; });
    return <div key={j} className={`pr-thought ${expanded ? "open" : ""}`}>
      <button className={`pr-row pr-think ${thinkingNow && last ? "now" : ""}`} aria-expanded={expandable ? expanded : undefined}
        disabled={!expandable} onClick={toggle}>
        <span className="pr-icon"><IconBulb size={14} /></span>
        <span className={`pr-label ${thinkingNow && last ? "shimmer" : ""}`}>{part.title}</span>
        {last && item.seconds >= 1 ? <span className="pr-time">{Math.round(item.seconds)} 秒</span> : null}
        {expandable ? <span className="pr-chev"><IconChevronDown size={13} /></span> : null}
      </button>
      {expandable ? <div className="collapse" data-open={expanded} inert={!expanded}>
        <div><div className="pr-thought-body"><Markdown text={part.body} /></div></div>
      </div> : null}
    </div>;
  })}</>;
}

function StepRow({ step, active, label }: { step: Step; active: boolean; label: string }) {
  const status = step.status === "running" && !active ? "note" : step.status;
  const sources = step.sources || [];
  return <li className={`step ${status}`}>
    <span className="pr-icon"><StepIcon name={step.name} /></span>
    <div className="step-body">
      <span className="step-label">{label}</span>
      {step.detail ? <span className="step-detail" title={step.detail}>{step.detail}</span> : null}
      {sources.length ? <span className="pr-sources">
        {sources.slice(0, 4).map((s) => <a key={s.url} href={s.url} target="_blank" rel="noreferrer noopener" title={s.title || s.url}>{s.site || siteOf(s.url)}</a>)}
        {sources.length > 4 ? <span className="pr-more">+{sources.length - 4}</span> : null}
      </span> : null}
      {step.output ? <details><summary>输出</summary><pre className="log">{step.output}</pre></details> : null}
    </div>
    <Status status={status} />
  </li>;
}

/** What the agent is doing right now, for the folded header of a live run. */
function current(items: Item[] | null | undefined, thinking: string): string {
  const last = items?.[items.length - 1];
  if (!last) return "";
  if (last.kind === "thinking") return thoughtOutline(last.text).at(-1)?.title || thinking;
  if (last.kind === "steps") {
    const step = last.steps[last.steps.length - 1];
    return step ? [step.label, step.detail].filter(Boolean).join(" · ") : "";
  }
  return "";
}

export default function WorkProcess({ run, items, live, loading, error, more, onLoad, renderAsk }: {
  run: Run;
  items: Item[] | null | undefined;
  live: boolean;
  loading: boolean;
  error: string;
  more: boolean;
  onLoad: () => void;
  renderAsk: (item: Extract<Item, { kind: "ask" }>) => React.ReactNode;
}) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const active = ["queued", "running", "waiting_user", "interrupted"].includes(run.status);
  const open = expanded ?? active;
  const chat = run.options?.mode === "chat";
  const thinking = chat ? "思考" : "梳理任务";
  const scroller = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  const running = run.status === "running" || run.status === "queued";

  // follow the newest step while the run works, unless the user scrolled up to read
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && open && stick.current) el.scrollTop = el.scrollHeight;
  }, [items, open]);

  const doing = running && !open ? current(items, thinking) : "";
  return <section className={`work-process steps ${open ? "open" : ""} ${running ? "is-live" : ""}`} aria-label={chat ? "思考过程" : "工作过程"}>
    <button className="steps-head" aria-expanded={open} onClick={() => {
      setExpanded(!open);
      if (!open && !items && !loading) onLoad();
    }}>
      <span className={`dot ${running ? "spin" : run.status === "failed" ? "failed" : ""}`} />
      <span className={running ? "shimmer" : ""}>{workHeading(run)}</span>
      {doing ? <span className="steps-now">{doing}</span> : null}
      <span className="chev"><IconChevronDown size={14} /></span>
    </button>
    <div className="collapse" data-open={open} inert={!open}>
      <div>
        <div className="work-timeline" ref={scroller} onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}>
          {items?.map((item, i) => <div key={i} className={`work-entry process-${item.kind}`}>
            {item.kind === "text" ? (item.text.trim() ? <div className="pr-narration"><Markdown text={item.text} /></div> : null)
              : item.kind === "thinking" ? <Thinking item={item} label={thinking} live={live} />
              : item.kind === "ask" ? renderAsk(item)
              : item.kind === "steer" ? <div className="pr-steer"><IconCornerDownRight size={13} /> 收到补充</div>
              : item.kind === "plan" ? <ol className="steps-list" aria-label="计划">{item.steps.map((step) => <li key={step.id} className={`step plan-step ${MARK[step.status]}`}>
                <span className="step-mark" /><div className="step-body"><span className="step-label">{step.title}</span></div>
              </li>)}</ol>
              : <ol className="steps-list">{item.steps.map((step, j) => <StepRow key={j} step={step} active={active} label={step.label} />)}</ol>}
          </div>)}
          {items && !items.length && !loading && !active ? <div className="hint">没有可显示的过程</div> : null}
          {loading ? <div className="pr-loading"><span /><span /><span /></div> : null}
          {error ? <button className="link" onClick={onLoad}>加载失败，重试</button> : null}
          {more && !loading ? <button className="link" onClick={onLoad}>加载更多过程</button> : null}
        </div>
      </div>
    </div>
  </section>;
}
