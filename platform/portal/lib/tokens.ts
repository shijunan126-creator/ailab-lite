/** Rough tokens of text being typed: CJK characters about 0.7 each, other text about 4 characters per token.
 * Only a guide while typing; the model's own count is what is billed. */
export function estimateTokens(text: string): number {
  let cjk = 0;
  for (const ch of text) if (/[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]/.test(ch)) cjk++;
  return Math.ceil(cjk * 0.7 + (text.length - cjk) / 4);
}
