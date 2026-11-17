// Finding TeX in text. Answers use $$…$$ or \[…\] for display and \(…\) or $…$ inline; a single $ follows
// Pandoc's rule so prices stay text: no space just inside either $, and no digit right after the closing one.

/** Inline math, as one regex alternative (no capture groups, so callers can embed it). */
export const MATH_INLINE = String.raw`\$\$(?:[^$\\]|\\[\s\S])+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?=[^\s$])(?:[^$\\\n]|\\.)+?(?<!\s)\$(?!\d)`;

export type Tex = { tex: string; display: boolean };

/** A match of MATH_INLINE as TeX and whether it was written for display. */
export function texOf(raw: string): Tex {
  if (raw.startsWith("$$")) return { tex: raw.slice(2, -2), display: true };
  if (raw.startsWith("\\[")) return { tex: raw.slice(2, -2), display: true };
  if (raw.startsWith("\\(")) return { tex: raw.slice(2, -2), display: false };
  return { tex: raw.slice(1, -1), display: false };
}

export type MathPiece = string | (Tex & { raw: string });

/** Text split into plain runs and math, with `\$` read as a literal dollar. */
export function splitMath(text: string): MathPiece[] {
  const out: MathPiece[] = [];
  const re = new RegExp(String.raw`(\\\$)|(` + MATH_INLINE + ")", "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const pending = text.slice(last, m.index) + (m[1] ? "$" : "");
    if (m[1]) {
      out.push(pending);
    } else {
      if (pending) out.push(pending);
      out.push({ ...texOf(m[2]), raw: m[2] });
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  // adjacent plain runs (around an escaped dollar) read as one
  return out.reduce<MathPiece[]>((acc, p) => {
    if (typeof p === "string" && typeof acc[acc.length - 1] === "string") acc[acc.length - 1] += p;
    else if (p !== "") acc.push(p);
    return acc;
  }, []);
}

// Environments KaTeX typesets that answers write without $$ around them.
const ENV = /^\s*\\begin\{(equation|align|alignat|gather|multline|flalign|eqnarray|aligned|gathered|split|cases|dcases|rcases|[pbBvV]?matrix|smallmatrix|array|darray|CD)(\*?)\}/;

function closer(line: string): { open: string; close: string; whole: boolean } | null {
  const t = line.trimStart();
  if (t.startsWith("$$")) return { open: "$$", close: "$$", whole: false };
  if (t.startsWith("\\[")) return { open: "\\[", close: "\\]", whole: false };
  const env = ENV.exec(line);
  if (env) return { open: "", close: `\\end{${env[1]}${env[2]}}`, whole: true };
  return null;
}

/** Whether a line opens a display block (as opposed to a paragraph that merely starts with inline display math). */
export function opensMath(line: string): boolean {
  const c = closer(line);
  if (!c) return false;
  const t = line.trim();
  const at = t.indexOf(c.close, c.open.length || 1);
  return at < 0 || !t.slice(at + c.close.length).trim();
}

export type MathBlock = Tex & { next: number; closed: boolean; rest?: string };

/** The display block starting at `lines[i]`: its TeX, the line after it, and text after the closing delimiter on
 * that line (`rest`). An unclosed block runs to the end of the text (still arriving); a blank line first means it
 * was not math. */
export function readMathBlock(lines: string[], i: number): MathBlock | null {
  const c = closer(lines[i]);
  if (!c || !opensMath(lines[i])) return null;
  const first = lines[i].trim();
  const body: string[] = [];
  let line = c.whole ? first : first.slice(c.open.length);
  let j = i;
  for (;;) {
    const at = line.indexOf(c.close);
    if (at >= 0) {
      body.push(c.whole ? line.slice(0, at + c.close.length) : line.slice(0, at));
      const rest = line.slice(at + c.close.length).trim();
      return { tex: body.join("\n").trim(), display: true, next: j + 1, closed: true, rest: rest || undefined };
    }
    body.push(line);
    j++;
    if (j >= lines.length) return { tex: body.join("\n").trim(), display: true, next: j, closed: false };
    if (!lines[j].trim()) return null;
    line = lines[j];
  }
}

/** While an answer streams, an inline `\(` on its last line whose `\)` has not arrived yet: hide it until it does. */
export function holdOpenMath(text: string): string {
  const lineStart = text.lastIndexOf("\n") + 1;
  const open = text.lastIndexOf("\\(");
  if (open < lineStart || text.indexOf("\\)", open) >= 0) return text;
  // inside a code block it is code
  if ((text.slice(0, open).match(/^\s*```/gm)?.length ?? 0) % 2) return text;
  return text.slice(0, open).trimEnd();
}

/** Table cells split on `|`, but not on an escaped `\|` or one inside inline code or math (where `\|` is a norm). */
export function tableCells(row: string): string[] {
  const s = row.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "");
  const cells: string[] = [];
  const spans = new RegExp("`[^`]*`|" + MATH_INLINE + String.raw`|\\\||\|`, "g");
  let cell = "";
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = spans.exec(s))) {
    cell += s.slice(last, m.index);
    if (m[0] === "|") {
      cells.push(cell);
      cell = "";
    } else cell += m[0] === "\\|" ? "|" : m[0];
    last = m.index + m[0].length;
  }
  cells.push(cell + s.slice(last));
  return cells.map((c) => c.trim());
}
