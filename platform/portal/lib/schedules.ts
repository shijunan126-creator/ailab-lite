import { formatDate } from "./format";
import type { Schedule } from "./api";

export const SCHEDULE_LIMIT = 5; // the API enforces the same
export const DAYS = ["一", "二", "三", "四", "五", "六", "日"];
export const MODE_LABEL: Record<Schedule["mode"], string> = { chat: "对话", work: "工作" };

const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** 11月12日, with the year only when it is not this year. */
export function dayText(value: string): string {
  const [y, m, d] = value.split("-").map(Number);
  return `${y === new Date().getFullYear() ? "" : `${y}年`}${m}月${d}日`;
}

/** 每天 09:00 / 工作日 09:00 / 每周一、三 09:00 / 11月12日 09:00 */
export function when(s: Schedule["schedule"]): string {
  if (s.kind === "daily") return `每天 ${s.time}`;
  if (s.kind === "weekdays") return `工作日 ${s.time}`;
  if (s.kind === "once") return `${s.date ? dayText(s.date) : ""} ${s.time}`.trim();
  const days = [...(s.days || [])].sort().map((d) => DAYS[d]).join("、");
  return `每周${days || "—"} ${s.time}`;
}

/** "今天 09:00" / "明天 09:00" / the date for anything later. */
export function nextText(value: string | null): string {
  if (!value) return "";
  const at = new Date(value);
  const diff = Math.round((dayStart(at) - dayStart(new Date())) / 86_400_000);
  const time = at.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return diff === 0 ? `今天 ${time}` : diff === 1 ? `明天 ${time}` : formatDate(value).replace(/:\d{2}$/, "");
}

/** A scheduled turn's own time ("2026-11-12 09:00 周四", already in the task's zone): 今天 09:00 / 11月12日 09:00. */
export function occurrenceText(at: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})/.exec(at);
  if (!match) return at;
  const [, y, m, d, time] = match;
  const now = new Date();
  if (+y === now.getFullYear() && +m === now.getMonth() + 1 && +d === now.getDate()) return `今天 ${time}`;
  return `${dayText(`${y}-${m}-${d}`)} ${time}`;
}

/** What a task is doing now, in a few words: shown under its title and on its pill in the conversation. */
export function stateText(s: Schedule): string {
  if (!s.enabled) {
    if (s.paused_reason === "done") return "已完成";
    if (s.paused_reason === "ended") return "已到结束日期";
    if (s.paused_reason === "unread") return "结果一直没人看，已暂停";
    if (s.paused_reason === "failures") return "连续失败，已暂停";
    return "已停用";
  }
  if (s.due_at) return "等这条回答结束后执行";
  return s.next_run_at ? `下次 ${nextText(s.next_run_at)}` : "";
}
