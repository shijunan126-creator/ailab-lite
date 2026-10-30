/** An answer split into Markdown and generated-UI parts (HLD §6.5). The same rules run in Python
 * (`domain/answer_parts.py`); both read the fixtures in `platform/tests/fixtures/genui/`. */

export type AnswerPart = { kind: "md" | "ui"; text: string; closed: boolean; index: number };

const FENCE = /^(`{3,}|~{3,})(.*)$/;

/** While streaming, a last line that may still become a fence ("``", "```op") waits for its newline. */
function heldBack(line: string): boolean {
  const m = /^(`+|~+)([A-Za-z]*)$/.exec(line.replace(/\r$/, ""));
  if (!m) return false;
  return m[1].length < 3 ? !m[2] : "openui".startsWith(m[2]);
}

/** Text is kept byte for byte (line ends, blank lines), as in the shared fixtures. Only a line-start ```openui
 * opens a block; it closes with the same character, at least as long, with no info. Any other fence is code,
 * and an openui fence inside it is code too. */
export function splitAnswerParts(source: string, streaming = false): AnswerPart[] {
  let text = source;
  if (streaming) {
    const cut = text.lastIndexOf("\n") + 1;
    if (heldBack(text.slice(cut))) text = text.slice(0, cut);
  }
  const parts: AnswerPart[] = [];
  const add = (kind: AnswerPart["kind"], value: string, closed: boolean) => parts.push({ kind, text: value, closed, index: parts.length });
  let start = 0;
  let body = 0;
  let offset = 0;
  let fence = "";
  let ui = false;
  while (offset < text.length) {
    const nl = text.indexOf("\n", offset);
    const end = nl < 0 ? text.length : nl + 1;
    const m = FENCE.exec(text.slice(offset, end).replace(/\r?\n$/, ""));
    if (m) {
      const [, ticks, info] = m;
      if (fence) {
        if (ticks[0] === fence[0] && ticks.length >= fence.length && !info.trim()) {
          if (ui) {
            add("ui", text.slice(body, offset), true);
            start = end;
          }
          fence = "";
          ui = false;
        }
      } else {
        fence = ticks;
        ui = ticks[0] === "`" && info.trim() === "openui";
        if (ui) {
          if (offset > start) add("md", text.slice(start, offset), true);
          start = offset;
          body = end;
        }
      }
    }
    offset = end;
  }
  if (ui) add("ui", text.slice(body), false);
  else if (start < text.length) add("md", text.slice(start), true);
  return parts;
}

/** Whether a prefix of the answer ends inside an open openui fence (used to release UI source at once, not letter by letter). */
export function insideUiFence(text: string): boolean {
  const parts = splitAnswerParts(text);
  const last = parts[parts.length - 1];
  return !!last && last.kind === "ui" && !last.closed;
}

/** Plain text for an answer: each UI block becomes what its components would say in Markdown. `index` counts UI
 * blocks only, as the page numbers them for their saved state. */
export function answerMarkdown(text: string, uiToMarkdown: (source: string, index: number) => string): string {
  let blocks = 0;
  return splitAnswerParts(text)
    .map((p) => (p.kind === "md" ? p.text : uiToMarkdown(p.text, blocks++)).trim())
    .filter(Boolean)
    .join("\n\n");
}
