/** Split at block boundaries, preserving every character and fenced code/table blocks. */
export function markdownChunks(text: string, size = 6000): string[] {
  const chunks: string[] = [];
  let start = 0, offset = 0, fence = false;
  for (const line of text.split(/(?<=\n)/)) {
    if (/^\s*```/.test(line)) fence = !fence;
    offset += line.length;
    if (!fence && offset - start >= size && (!line.trim() || (offset - start >= size * 2 && !/^\s*(```|\|)/.test(line)))) {
      chunks.push(text.slice(start, offset));
      start = offset;
    }
  }
  if (start < text.length) chunks.push(text.slice(start));
  return chunks;
}
