// Saving a skill creates a new file version that the Worker must scan before it can be used. Kept free of
// React and fetch so the waiting/locking rules can be tested on their own (tests/skill-save.test.mjs).

export type SkillContent = {
  name: string;
  content: string | null; // null for a ZIP: its scripts are edited as a package, not as text
  file_id: string | null;
  editable: boolean;
  kind: "md" | "zip" | "builtin";
  revision: string | null;
};

export type ScannedFile = { id: string; status: string; meta?: Record<string, any> | null };

/** GET /v1/skills lists a head that is still being scanned with this error. */
export const SCANNING = "安全扫描中";

/** A head that is still being scanned must not be overwritten again: the next version would branch from it unseen. */
export function isLocked(skill: { file_id?: string; error?: string | null }, scanning: ReadonlySet<string>): boolean {
  return skill.error === SCANNING || (!!skill.file_id && scanning.has(skill.file_id));
}

/** Poll a file until it leaves `pending`. Resolves null when the deadline passes; rejects with the abort reason. */
export async function waitForScan(
  fileId: string,
  getFile: (id: string, signal: AbortSignal) => Promise<ScannedFile | null>,
  { signal, deadlineMs = 60_000, intervalMs = 1000, now = Date.now }: { signal: AbortSignal; deadlineMs?: number; intervalMs?: number; now?: () => number }
): Promise<ScannedFile | null> {
  const deadline = now() + deadlineMs;
  while (now() < deadline) {
    await sleep(Math.min(intervalMs, Math.max(0, deadline - now())), signal);
    const file = await getFile(fileId, signal).catch((e) => {
      if (signal.aborted) throw e;
      return null; // a transient failure: try again until the deadline
    });
    if (file && file.status !== "pending") return file;
  }
  return null;
}

/** The `name:` of a SKILL.md front matter, which a new skill is saved under; "" when there is none. */
export function frontmatterName(markdown: string): string {
  const head = /^﻿?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(markdown);
  const line = head?.[1].split(/\r?\n/).find((l) => /^name\s*:/.test(l));
  return line ? line.replace(/^name\s*:\s*/, "").trim().replace(/^(["'])(.*)\1$/, "$2") : "";
}

/** What went wrong with a scanned version, or "" when it is a usable skill. */
export function scanProblem(file: ScannedFile): string {
  if (file.status === "rejected") return "没有通过安全扫描";
  if (file.meta?.skill_error) return String(file.meta.skill_error);
  if (!file.meta?.skill) return "不是技能文件（需要带 name、description 的 SKILL.md，或包含它的 .zip）";
  return "";
}

/** A failed save, in words. 422 is the server's compare-and-set: the skill changed since it was opened. */
export function saveError(status: number, body: string): string {
  if (status === 422) return detailOf(body) || "技能已在别处修改，请关闭后重新打开";
  if (status === 413) return "超出大小上限";
  return `保存失败（${status}）${detailOf(body) ? `：${detailOf(body)}` : ""}`;
}

export function loadError(status: number, body: string): string {
  return detailOf(body) || `加载失败（${status}）`;
}

function detailOf(body: string): string {
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed?.detail === "string") return parsed.detail;
    if (Array.isArray(parsed?.detail)) return parsed.detail.map((d: any) => d?.msg).filter(Boolean).join("；");
  } catch {}
  return "";
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
