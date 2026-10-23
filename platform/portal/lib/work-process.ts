import type { EventItem, Run } from "./api";

export type Step = { name: string; label: string; detail?: string; status: "running" | "ok" | "failed" | "note"; output?: string; modelStep?: number };
export type PlanStep = { id: string; title: string; status: "pending" | "running" | "done" | "skipped" | "failed" };
export type Item =
  | { kind: "plan"; steps: PlanStep[] }
  | { kind: "ask"; id: string; question: string; options: string[]; allowText: boolean; answer?: string }
  | { kind: "text"; step: number; text: string }
  | { kind: "thinking"; step: number; text: string; seconds: number; done: boolean }
  | { kind: "steps"; steps: Step[] };

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

/** Fold one event of a run into its display items (text segments interleaved with groups of steps). */
export function reduceEvent(items: Item[], event: EventItem): Item[] {
  const p = event.payload || {};
  const next = items.map((it) => (it.kind === "steps" ? { kind: "steps" as const, steps: it.steps.map((s) => ({ ...s })) } : { ...it }));
  // anything else the model does means it has stopped thinking
  if (event.type !== "agent.thinking")
    for (const it of next) {
      if (it.kind === "thinking") it.done = true;
      if (it.kind === "steps") for (const s of it.steps) if (s.name === "thinking" && s.status === "running") s.status = "note";
    }
  const note = (label: string, detail?: string, status: Step["status"] = "note") => lastSteps(next).push({ name: event.type, label, detail, status });
  switch (event.type) {
    case "agent.thinking": {
      const last = next[next.length - 1];
      if (last && last.kind === "steps") {
        // thinking between tool calls belongs to the work: one entry in the steps, not a block of its own
        const prev = last.steps[last.steps.length - 1];
        if (prev && prev.name === "thinking" && prev.status === "running") prev.detail = "正在梳理任务";
        else last.steps.push({ name: "thinking", label: "思考", status: "running" });
        last.steps[last.steps.length - 1].detail = `${Math.max(1, Math.round(p.seconds || 0))} 秒`;
        break;
      }
      if (last && last.kind === "thinking" && last.step === p.step) {
        last.seconds = p.seconds ?? last.seconds;
      } else next.push({ kind: "thinking", step: p.step, text: "", seconds: p.seconds || 0, done: false });
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
      note("已整理较早的对话", p.turns ? `${p.turns} 轮` : undefined);
      break;
    case "agent.skill":
      note("已加载技能", p.title || SKILL_LABEL[p.name] || p.name, "ok");
      break;
    case "agent.attachments":
      for (const f of p.files || [])
        note("已读取附件", `${f.filename}（${f.chars} 字${f.truncated ? "，内容较长，只读了开头部分" : ""}）`, "ok");
      break;
    case "agent.sandbox":
      note("沙箱已就绪");
      break;
    case "agent.waiting_sandbox":
      note("等待空闲沙箱…");
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
      note("自动归档", p.filename);
      break;
    default:
      break;
  }
  return next;
}

export function workHeading(run: Pick<Run, "status" | "work_ms" | "started_at">): string {
  const duration = typeof run.work_ms === "number" ? `${Math.floor(run.work_ms / 60000)}分${Math.floor(run.work_ms / 1000) % 60}秒` : null;
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
