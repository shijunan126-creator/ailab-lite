"use client";

import { memo, useEffect, useLayoutEffect, useRef } from "react";
import type { Run } from "../../lib/api";
import { useFollow } from "../../lib/use-follow";
import { type Item, type PlanStep, type Step, siteOf, thoughtOutline, workHeading } from "../../lib/work-process";
import { IconBulb, IconBook, IconCheck, IconChevronDown, IconFile, IconFilePen, IconGlobe, IconSearch, IconSparkles, IconTerminal, IconX, IconCornerDownRight } from "../icons";
import { useViewport } from "../viewport-block";
import { useCollapse } from "../motion/use-collapse";
import { useSmoothShrink } from "../motion/use-smooth-shrink";
import Markdown from "../markdown";
import Answer from "./answer";

const MARK: Record<PlanStep["status"], string> = { pending: "pending", running: "running", done: "ok", skipped: "note", failed: "failed" };

// Older runs keep their tool names; the icon must still say what happened.
const SEARCH_TOOLS = new Set(["web_search", "search_images", "search_library", "recall_history", "grep", "find"]);
const GLOBE_TOOLS = new Set(["read_url", "download_url"]);
const TERMINAL_TOOLS = new Set(["bash", "bash_wait", "bash_kill", "exec", "exec_wait", "exec_kill", "fetch_packages", "fetch_repo"]);
const EDIT_TOOLS = new Set(["write", "edit", "write_file", "save_skill"]);
const BOOK_TOOLS = new Set(["read", "read_file", "read_library_file", "copy_library_file", "list_files"]);

function StepIcon({ name }: { name: string }) {
  if (SEARCH_TOOLS.has(name)) return <IconSearch size={14} />;
  if (GLOBE_TOOLS.has(name)) return <IconGlobe size={14} />;
  if (TERMINAL_TOOLS.has(name)) return <IconTerminal size={14} />;
  if (EDIT_TOOLS.has(name)) return <IconFilePen size={14} />;
  if (BOOK_TOOLS.has(name)) return <IconBook size={14} />;
  if (name === "load_skill") return <IconSparkles size={14} />;
  return <IconFile size={14} />;
}

function Status({ status }: { status: Step["status"] }) {
  if (status === "running") return <span className="pr-spin" aria-label="进行中" />;
  if (status === "failed") return <span className="pr-status failed" aria-label="失败"><IconX size={12} /></span>;
  if (status === "ok") return <span className="pr-status ok" aria-label="完成"><IconCheck size={12} /></span>;
  return null;
}

function Thinking({ item, live, choice, onToggle, label }: {
  live: boolean; item: Extract<Item, { kind: "thinking" }>; choice: boolean | undefined; onToggle: (open: boolean) => void; label: string;
}) {
  const thinkingNow = live && !item.done;
  // A thought reads live while the model writes it and folds the moment the model does anything else; a reader's choice wins.
  const expanded = choice ?? thinkingNow;
  // Only the header needs a short preview; the body always receives the original text.
  const title = thoughtOutline(item.text.slice(0, 500))[0]?.title || label;
  return <div className={`pr-thought ${expanded ? "open" : ""}`} data-thinking-step={item.step}>
    <button className={`pr-row pr-think ${thinkingNow ? "now" : ""}`} aria-expanded={expanded} onClick={() => onToggle(!expanded)}>
      <span className="pr-icon"><IconBulb size={14} /></span>
      <span className={`pr-label ${thinkingNow ? "shimmer" : ""}`}>{title}</span>
      {item.seconds >= 1 ? <span className="pr-time">{Math.round(item.seconds)} 秒</span> : null}
      <span className="pr-chev"><IconChevronDown size={13} /></span>
    </button>
    {expanded ? <div className="pr-thought-body"><Markdown virtual text={item.text} streaming={thinkingNow} /></div> : null}
  </div>;
}

// steps keep their identity across events (structural sharing), so a new step does not re-render the others
const StepRow = memo(function StepRow({ step, active, label }: { step: Step; active: boolean; label: string }) {
  const viewport = useViewport<HTMLLIElement>(step.status === "running" && active, 42);
  const status = step.status === "running" && !active ? "note" : step.status;
  const sources = step.sources || [];
  return <li ref={viewport.ref} data-mounted={viewport.visible} style={viewport.style} className={`step ${status}`}>
    {viewport.visible ? <>
    <span className="pr-icon"><StepIcon name={step.name} /></span>
    <div className="step-body">
      <span className="step-label">{label}{step.background ? " · 仍在运行" : ""}</span>
      {step.detail ? <span className="step-detail" title={step.detail}>{step.detail}</span> : null}
      {sources.length ? <span className="pr-sources">
        {sources.slice(0, 4).map((s) => <a key={s.url} href={s.url} target="_blank" rel="noreferrer noopener" title={s.title || s.url}>{s.site || siteOf(s.url)}</a>)}
        {sources.length > 4 ? <span className="pr-more">+{sources.length - 4}</span> : null}
      </span> : null}
      {step.output ? <details><summary>输出</summary><pre className="log">{step.output}</pre></details> : null}
    </div>
    <Status status={status} />
    </> : null}
  </li>;
});

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

export default function WorkProcess({ thoughts, onThought, run, items, live, loading, error, more, onLoad, renderAsk, expanded, onExpanded }: {
  /** the reader's choice per thought, by model step; a thought without one follows whether it is being written */
  thoughts: Map<number, boolean>;
  onThought: (step: number, open: boolean) => void;
  expanded: boolean | null;
  onExpanded: (value: boolean) => void;
  run: Run;
  items: Item[] | null | undefined;
  live: boolean;
  loading: boolean;
  error: string;
  more: boolean;
  onLoad: () => void;
  renderAsk: (item: Extract<Item, { kind: "ask" }>) => React.ReactNode;
}) {
  const active = ["queued", "running", "waiting_user", "interrupted"].includes(run.status);
  const open = expanded ?? active;
  const fold = useCollapse(open);
  const chat = run.options?.mode === "chat";
  const thinking = chat ? "思考" : "梳理任务";
  const scroller = useRef<HTMLDivElement | null>(null);
  const sentinel = useRef<HTMLDivElement | null>(null);
  const running = run.status === "running" || run.status === "queued" || !!run.result?.file_wait;
  // the newest step and the thought being written stay in view while the run works, unless the reader scrolls up
  const follow = useFollow(scroller, { enabled: open && active });
  // a thought folding at the answer shrinks the box; it glides so the answer below does not jump
  useSmoothShrink(scroller);
  useLayoutEffect(() => {
    if (open && active) follow.settle(true);
  }, [open, active, follow]);

  useEffect(() => {
    if (!open || !more || loading || error || !sentinel.current) return;
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) onLoad(); }, { root: scroller.current, rootMargin: "400px" });
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [open, more, loading, error, onLoad]);

  useEffect(() => {
    if (open && !items && !loading && !error) onLoad();
  }, [open, items, loading, error, onLoad]);

  const doing = running && !open ? current(items, thinking) : "";
  return <section className={`work-process steps ${open ? "open" : ""} ${running ? "is-live" : ""}`} aria-label={chat ? "思考过程" : "工作过程"}>
    <button className="steps-head" aria-expanded={open} onClick={() => {
      onExpanded(!open);
      if (!open && !items && !loading) onLoad();
    }}>
      <span className={`dot ${running ? "spin" : run.status === "failed" ? "failed" : ""}`} />
      <span className={running ? "shimmer" : ""}>{workHeading(run)}</span>
      {doing ? <span className="steps-now">{doing}</span> : null}
      <span className="chev"><IconChevronDown size={14} /></span>
    </button>
    <div className="collapse" data-open={open} inert={!open} ref={fold.ref}>
      <div>
        <div className="work-timeline" ref={scroller} onScroll={follow.onScroll}>
          <div ref={follow.content}>
          {fold.body ? items?.map((item, i) => <div key={i} className={`work-entry process-${item.kind}`}>
            {item.kind === "text" ? (item.text.trim() ? <div className="pr-narration"><Answer virtual text={item.text} streaming={live && i === items.length - 1} /></div> : null)
              : item.kind === "thinking" ? <Thinking item={item} live={live} label={thinking} choice={thoughts.get(item.step)} onToggle={(value) => onThought(item.step, value)} />
              : item.kind === "ask" ? renderAsk(item)
              : item.kind === "steer" ? <div className="pr-steer"><IconCornerDownRight size={13} /> 收到补充</div>
              : item.kind === "plan" ? <ol className="steps-list" aria-label="计划">{item.steps.map((step) => <li key={step.id} className={`step plan-step ${MARK[step.status]}`}>
                <span className="step-mark" /><div className="step-body"><span className="step-label">{step.title}</span></div>
              </li>)}</ol>
              : <ol className="steps-list">{item.steps.map((step, j) => <StepRow key={j} step={step} active={active} label={step.label} />)}</ol>}
          </div>) : null}
          {items && !items.length && !loading && !running && run.status !== "waiting_user" ? <div className="hint">没有可显示的过程</div> : null}
          {loading ? <div className="pr-loading"><span /><span /><span /></div> : null}
          {error ? <button className="link" onClick={onLoad}>加载失败，重试</button> : null}
          {more ? <div ref={sentinel} className="process-more">{!loading ? <button className="link" onClick={onLoad}>继续加载</button> : null}</div> : null}
          </div>
        </div>
      </div>
    </div>
  </section>;
}
