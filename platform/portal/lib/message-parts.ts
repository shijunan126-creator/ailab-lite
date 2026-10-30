// A message is text with skills placed inside it. The API keeps the text as `prompt` and each skill as
// `{name, file_id, at}`, where `at` counts Unicode code points (Python len()), never UTF-16 units: an emoji before a
// skill must not move it. Pure functions, shared by the composer, the edit box and the sent message.

export type SkillRef = { name: string; title: string; fileId?: string | null };
export type Part = { kind: "text"; text: string } | ({ kind: "skill" } & SkillRef);
// title stays in the browser (the pending message shows it); the API resolves names itself
export type SkillPick = { name: string; file_id?: string | null; at: number; title?: string };
/** How a run remembers the skills it was sent with (`options.skills`); older runs have only `skill`. */
export type SentSkill = { name: string; title?: string; kind?: string; file_id?: string | null; at?: number | null };

export const MAX_SKILLS = 5;


const points = (s: string) => Array.from(s).length;

/** Adjacent text merged, empty text dropped: two equal messages always have equal parts. */
export function normalize(parts: Part[]): Part[] {
  const out: Part[] = [];
  for (const p of parts) {
    if (p.kind === "text") {
      if (!p.text) continue;
      const last = out[out.length - 1];
      if (last?.kind === "text") out[out.length - 1] = { kind: "text", text: last.text + p.text };
      else out.push({ kind: "text", text: p.text });
    } else out.push(p);
  }
  return out;
}

export const plainText = (parts: Part[]) => parts.map((p) => (p.kind === "text" ? p.text : "")).join("");
export const skillsOf = (parts: Part[]) => parts.filter((p): p is Extract<Part, { kind: "skill" }> => p.kind === "skill");

/** The text as sent (trimmed like before) and where each skill sits in it. A skill appears once: the first place wins. */
export function toPayload(parts: Part[]): { prompt: string; skills: SkillPick[] } {
  const raw = plainText(parts);
  const lead = points(raw) - points(raw.trimStart());
  const prompt = raw.trim();
  const end = points(prompt);
  const skills: SkillPick[] = [];
  let at = 0;
  for (const p of parts) {
    if (p.kind === "text") {
      at += points(p.text);
      continue;
    }
    if (skills.some((s) => s.name === p.name)) continue;
    skills.push({ name: p.name, file_id: p.fileId ?? null, at: Math.min(end, Math.max(0, at - lead)), title: p.title });
  }
  return { prompt, skills };
}

/** The other way: a sent prompt and its skills back into parts (sent message, edit box). Skills without a place go last. */
export function fromPayload(prompt: string, skills: SentSkill[] | undefined | null, titleOf?: (name: string) => string | undefined): Part[] {
  const chars = Array.from(prompt);
  const placed = (skills || [])
    .map((s, i) => ({ s, i, at: s.at == null ? chars.length : Math.min(chars.length, Math.max(0, s.at)) }))
    .sort((a, b) => a.at - b.at || a.i - b.i);
  const parts: Part[] = [];
  let from = 0;
  for (const { s, at } of placed) {
    parts.push({ kind: "text", text: chars.slice(from, at).join("") });
    parts.push({ kind: "skill", name: s.name, title: s.title || titleOf?.(s.name) || s.name, fileId: s.file_id ?? null });
    from = at;
  }
  parts.push({ kind: "text", text: chars.slice(from).join("") });
  return normalize(parts);
}

/** The skills a run was sent with, from `options.skills`, or the single `options.skill` of older runs. */
export function sentSkills(options?: { skills?: SentSkill[]; skill?: string; skill_title?: string } | null): SentSkill[] {
  if (options?.skills?.length) return options.skills;
  if (options?.skill) return [{ name: options.skill, title: options.skill_title, at: null }];
  return [];
}

/**
 * The "/" being typed right before the caret, if it opens the skill list: at the start, or after a space, a line
 * break or a skill. `A/B`, `https://x/y` and `~/dir` stay text. `before` is the text from the last boundary (start,
 * line break or skill) up to the caret.
 */
export function slashQuery(before: string): { query: string; length: number } | null {
  const m = /(?:^|[\s 　])\/([^\s 　/]{0,32})$/.exec(before);
  if (!m) return null;
  return { query: m[1], length: m[1].length + 1 };
}

/** Skills whose title or name contains the query (case-insensitive); title-prefix matches first. */
export function matchSkills<T extends { name: string; title: string }>(list: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return list;
  const hits = list.filter((s) => `${s.title}\u0000${s.name}`.toLowerCase().includes(q));
  const rank = (s: T) => (s.title.toLowerCase().startsWith(q) ? 0 : s.name.toLowerCase().startsWith(q) ? 1 : 2);
  return hits.map((s, i) => ({ s, i })).sort((a, b) => rank(a.s) - rank(b.s) || a.i - b.i).map((x) => x.s);
}
