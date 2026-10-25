import type { EventItem, Run } from "./api";

export type Source = { title: string; url: string; site: string };
export type Step = {
  name: string;
  label: string;
  detail?: string;
  status: "running" | "ok" | "failed" | "note";
  output?: string;
  modelStep?: number;
  sources?: Source[];
};
export type PlanStep = { id: string; title: string; status: "pending" | "running" | "done" | "skipped" | "failed" };
export type Item =
  | { kind: "plan"; steps: PlanStep[] }
  | { kind: "ask"; id: string; question: string; options: string[]; allowText: boolean; answer?: string }
  | { kind: "text"; step: number; text: string }
  | { kind: "thinking"; step: number; text: string; seconds: number; done: boolean }
  | { kind: "steps"; steps: Step[] }
  | { kind: "steer"; id: string; prompt: string }; // a message the user added while this answer was running

const SKILL_LABEL: Record<string, string> = { pptx: "PPT 生成", xlsx: "Excel 表格" };

function describeTool(name: string, args: any): { label: string; detail?: string } {
  switch (name) {
    case "load_skill":
      return { label: `读取技能 ${args?.name || ""}` };
    case "search_images":
      return { label: "搜索图片", detail: args?.query };
    case "exec":
      return { label: "运行命令", detail: String(args?.cmd || "").slice(0, 160) };
    case "write_file":
      return { label: "写入文件", detail: args?.path };
    case "read_file":
      return { label: "读取文件", detail: args?.path };
    case "list_files":
      return { label: "查看目录", detail: args?.path };
    case "deliver_file":
      return { label: "交付文件", detail: args?.filename || String(args?.path || "").split("/").pop() };
    case "web_search":
      return { label: "搜索网页", detail: args?.query };
    case "read_url":
      return { label: "读取网页", detail: args?.url };
    case "download_url":
      return { label: "下载文件", detail: args?.url };
    case "fetch_repo":
      return { label: "获取仓库", detail: [args?.repo, args?.ref].filter(Boolean).join("@") };
    case "fetch_packages":
      return { label: "安装依赖", detail: (Array.isArray(args?.packages) ? args.packages : []).join(" ") };
    case "save_skill":
      return { label: args?.mode === "update" ? "更新个人技能" : "保存个人技能", detail: args?.name };
    case "search_library":
      return { label: "搜索资料库", detail: args?.query };
    case "read_library_file":
      return { label: "阅读资料", detail: args?.filename || args?.file_id };
    case "copy_library_file":
      return { label: "复制到工作区", detail: args?.path || args?.filename || args?.file_id };
    case "memory":
      return { label: "更新记忆" };
    case "update_plan":
      return { label: "更新计划" };
    default:
      return { label: name };
  }
}

function lastSteps(items: Item[]): Step[] {
  const last = items[items.length - 1];
  if (last && last.kind === "steps") return last.steps;
  const group: Item = { kind: "steps", steps: [] };
  items.push(group);
  return group.steps;
}

/** Fold one event of a run into its display items: the model's reasoning, its text, and groups of tool steps, in order. */
export function reduceEvent(items: Item[], event: EventItem): Item[] {
  const p = event.payload || {};
  const next = items.map((it) => (it.kind === "steps" ? { kind: "steps" as const, steps: it.steps.map((s) => ({ ...s })) } : { ...it }));
  // anything else the model does means it has stopped thinking
  if (event.type !== "agent.thinking") for (const it of next) if (it.kind === "thinking") it.done = true;
  const note = (label: string, detail?: string, status: Step["status"] = "note") => lastSteps(next).push({ name: event.type, label, detail, status });
  switch (event.type) {
    case "agent.thinking": {
      const last = next[next.length - 1];
      if (last && last.kind === "thinking" && last.step === p.step && !last.done) {
        last.text += p.text || "";
        last.seconds = p.seconds ?? last.seconds;
      } else next.push({ kind: "thinking", step: p.step, text: p.text || "", seconds: p.seconds || 0, done: false });
      break;
    }
    case "agent.delta": {
      const last = next[next.length - 1];
      if (last && last.kind === "text" && last.step === p.step) last.text += p.text || "";
      else next.push({ kind: "text", step: p.step, text: p.text || "" });
      break;
    }
    case "agent.message": {
      const existing = [...next].reverse().find((it) => it.kind === "text" && it.step === p.step) as Extract<Item, { kind: "text" }> | undefined;
      if (existing) existing.text = p.content || "";
      else next.push({ kind: "text", step: p.step, text: p.content || "" });
      break;
    }
    case "agent.tool_call": {
      const d = describeTool(p.name, p.arguments);
      lastSteps(next).push({ name: p.name, ...d, status: "running", modelStep: p.step });
      break;
    }
    case "agent.tool_result": {
      for (let i = next.length - 1; i >= 0; i--) {
        const it = next[i];
        if (it.kind !== "steps") continue;
        const step = [...it.steps].reverse().find((s) => s.name === p.name && s.status === "running" && (p.step === undefined || s.modelStep === p.step));
        if (step) {
          step.status = p.ok ? "ok" : "failed";
          step.output = [p.error, p.stdout, p.stderr].filter(Boolean).join("\n").slice(0, 1500);
          if (Array.isArray(p.sources)) step.sources = p.sources.filter((x: any) => x && /^https?:\/\//.test(x.url || "")).slice(0, 10);
          break;
        }
      }
      break;
    }
    case "plan.updated": {
      const before = new Map(next.flatMap((it) => it.kind === "plan" ? it.steps : []).map((step) => [step.id, step]));
      const changed = (p.steps || []).filter((step: PlanStep) => {
        const old = before.get(step.id);
        return !old || old.title !== step.title || old.status !== step.status;
      });
      if (changed.length) next.push({ kind: "plan", steps: changed });
      break;
    }
    case "agent.ask_user":
      if (!next.some((it) => it.kind === "ask" && it.id === p.question_id))
        next.push({ kind: "ask", id: p.question_id, question: p.question || "", options: p.options || [], allowText: p.allow_text !== false });
      break;
    case "agent.steered":
      if (!next.some((it) => it.kind === "steer" && it.id === p.queued_id)) next.push({ kind: "steer", id: String(p.queued_id || ""), prompt: String(p.prompt || "") });
      break;
    case "agent.ask_answered":
      for (const it of next) if (it.kind === "ask" && it.id === p.question_id) it.answer = p.answer ?? "";
      break;
    case "run.resumed":
      note("从中断处继续", undefined, "ok");
      break;
    case "agent.model_fallback":
      note("已换用备用模型", [p.from_label || p.from, p.to_label || p.to].filter(Boolean).join(" → ") || undefined);
      break;
    case "agent.context_compacted":
      note("已压缩较早的对话", p.turns ? `${p.turns} 轮` : undefined);
      break;
    case "agent.skill":
      note("已加载技能", p.title || SKILL_LABEL[p.name] || p.name, "ok");
      break;
    case "agent.attachments":
      for (const f of p.files || [])
        note("已读取附件", `${f.filename}（${f.chars} 字${f.truncated ? "，内容较长，只读了开头部分" : ""}）`, "ok");
      break;
    case "agent.waiting_sandbox":
      note("排队中，马上开始…");
      break;
    case "preview.updated":
      note("预览已更新", p.filename, "ok");
      break;
    case "preview.failed":
      note("预览生成失败", [p.filename, p.error].filter(Boolean).join("：") || undefined, "failed");
      break;
    case "agent.warning":
      note("提示", p.message, "failed");
      break;
    case "agent.autodeliver":
      note("已存入资料库", p.filename);
      break;
    default:
      break;
  }
  return next;
}

export function workHeading(run: Pick<Run, "status" | "work_ms" | "started_at" | "options">): string {
  const ms = typeof run.work_ms === "number" ? run.work_ms : null;
  if (run.options?.mode === "chat") {
    // chat only answers: its time is thinking, not work
    const thought = ms === null ? null : ms < 60000 ? `${Math.max(1, Math.round(ms / 1000))}秒` : `${Math.floor(ms / 60000)}分${Math.floor(ms / 1000) % 60}秒`;
    switch (run.status) {
      case "running": return "正在思考…";
      case "queued": return "等待开始";
      case "waiting_user": return "等待你回答";
      case "interrupted": return "已暂停";
      case "failed": return "回答失败";
      case "canceled": return "已取消";
      default: return thought ? `已思考 ${thought}` : "已思考";
    }
  }
  const duration = ms === null ? null : `${Math.floor(ms / 60000)}分${Math.floor(ms / 1000) % 60}秒`;
  switch (run.status) {
    case "running": return "正在工作中…";
    case "queued": return run.started_at ? "等待继续" : "等待开始";
    case "waiting_user": return "等待你回答";
    case "interrupted": return "已暂停";
    case "failed": return duration ? `执行失败 · 工作总时长：${duration}` : "执行失败";
    case "canceled": return duration ? `已取消 · 工作总时长：${duration}` : "已取消";
    default: return duration ? `工作总时长：${duration}` : "工作已完成";
  }
}

/** A turn's display: the process (reasoning, tools, narration between them) and the answer, which is the text after the model's
 * last action. While the run streams, that text is shown as the answer at once; if a tool call follows, it becomes narration.
 * Notes the platform adds afterwards (a preview, a file saved) stay in the process without pulling the answer back in. */
export function splitTurn(items: Item[] | null | undefined): { process: Item[]; answer: string } {
  const list = items || [];
  const acts = (it: Item) => it.kind !== "text" && (it.kind !== "steps" || it.steps.some((s) => s.modelStep !== undefined));
  let cut = list.length;
  while (cut > 0 && !acts(list[cut - 1])) cut--;
  const tail = list.slice(cut);
  const answer = tail.map((it) => (it.kind === "text" ? it.text : "")).filter((t) => t.trim()).join("\n\n").trim();
  return { process: [...list.slice(0, cut), ...tail.filter((it) => it.kind !== "text")], answer };
}

export type Thought = { title: string; body: string };

/** The outline of one reasoning block. Models that summarise their thinking start each part with a bold line ("**Checking the data**");
 * those lines become the outline. Without them the block is one part named by its first sentence. */
export function thoughtOutline(text: string): Thought[] {
  const parts: Thought[] = [];
  let current: Thought | null = null;
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    const heading = /^\s*\*\*([^*]{1,80})\*\*\s*$/.exec(line) || /^\s*#{1,4}\s+(.{1,80})$/.exec(line);
    if (heading) {
      current = { title: heading[1].trim(), body: "" };
      parts.push(current);
    } else if (current) current.body += (current.body ? "\n" : "") + line;
    else if (line.trim()) {
      current = { title: "", body: line };
      parts.push(current);
    }
  }
  for (const part of parts) {
    part.body = part.body.trim();
    if (!part.title) {
      const first = /^[^。！？.!?\n]{1,60}[。！？.!?]?/.exec(part.body)?.[0] || part.body.slice(0, 40);
      // named by its first sentence: opening it shows the rest, not the same sentence again
      part.title = first.replace(/[。.!！?？]$/, "");
      part.body = part.body.slice(first.length).trim();
    }
  }
  return parts.filter((part) => part.title || part.body);
}

/** Every web page the turn read or found, once each, in the order they came up. */
export function turnSources(items: Item[] | null | undefined): Source[] {
  const seen = new Map<string, Source>();
  for (const it of items || []) if (it.kind === "steps") for (const s of it.steps) for (const src of s.sources || []) if (!seen.has(src.url)) seen.set(src.url, src);
  return [...seen.values()];
}

/** One address for one page: the fragment and a trailing slash don't make another source. */
export function urlKey(url: string): string {
  try {
    const u = new URL(url);
    u.hash = "";
    return u.href.replace(/\/$/, "");
  } catch {
    return url;
  }
}

/** The sources an answer links to, in its order, and the rest the turn came across. */
export function splitSources(sources: Source[], answer: string): { cited: Source[]; more: Source[] } {
  const byKey = new Map(sources.map((s) => [urlKey(s.url), s]));
  const cited = new Map<string, Source>();
  for (const m of answer.matchAll(/\]\((https?:\/\/[^)\s]+)/g)) {
    const key = urlKey(m[1]);
    const source = byKey.get(key);
    if (source && !cited.has(key)) cited.set(key, source);
  }
  return { cited: [...cited.values()], more: sources.filter((s) => !cited.has(urlKey(s.url))) };
}

/** example.com for https://www.example.com/a/b */
export function siteOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
