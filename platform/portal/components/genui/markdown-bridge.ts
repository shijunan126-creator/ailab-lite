// Turning UI source into Markdown needs the OpenUI parser, which lives in the lazily loaded GenUI chunk. That chunk
// registers its converter here once loaded (it always is when a block has rendered); until then copy keeps the source.
import { answerMarkdown } from "../../lib/answer-parts";

type BlockState = Record<string, unknown> | undefined;
let convert: ((source: string, state: BlockState) => string) | null = null;

export function registerMarkdown(fn: (source: string, state: BlockState) => string) {
  convert = fn;
}

/** The answer as plain Markdown: each UI block becomes its components' text (D-G8), computed from the state the
 * reader left its controls in, so a copy says what the page shows. */
export function answerForCopy(text: string, state?: (index: number) => BlockState): string {
  if (!text.includes("```openui")) return text;
  return answerMarkdown(text, (source, i) => (convert ? convert(source, state?.(i)) : "```openui\n" + source + "\n```"));
}
