// AILab's bounded wire adapter. Location validation and text extraction use the pinned runtime itself.
import {readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname} from 'node:path';
import JSZip from 'jszip';
import {PARSERS} from '../../../vendor/office-suite/upstream/office-runtime/src/adapters/parsers.ts';
import {parseLocator, formatLocator} from '../../../vendor/office-suite/upstream/office-runtime/src/domain/anchor.ts';

const started = performance.now();
const [source, output, requestPath] = process.argv.slice(2);
const request = JSON.parse(await readFile(requestPath, 'utf8'));
const kind = source.split('.').pop()!;
const parser = PARSERS[kind as keyof typeof PARSERS];
if (!parser) throw Error('unsupported');
const assets = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json')) + '/';
const bytes = await readFile(source);
const doc = await parser(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength), 'document.' + kind,
    {cMapUrl: assets + 'cmaps/', standardFontDataUrl: assets + 'standard_fonts/'});
let total = 0;
const selections = (request.selections ?? []).map((at: string) => {
  const locator = doc.check(parseLocator(kind as any, at));
  let cursor: number | null = 0, text = '';
  do {
    const page = doc.read(locator, cursor);
    text += page.text + '\n';
    total += page.text.length;
    if (text.length > 200000 || total > 400000) throw Error('output_limit');
    if (page.next !== null && page.next <= cursor) throw Error('cursor_stalled');
    cursor = page.next;
  } while (cursor !== null);
  // A clicked PPT table quotes cells joined with " | ", without Markdown borders/header rules.
  // Decode only the parser's table framing; keep cell punctuation and escaped literal pipes.
  if (locator.kind === 'pptx' && locator.shape) {
    const lines = text.split('\n');
    // Labels may carry a name and a slide position: [shape:N table "Name" @x..% y..%].
    const label = `[shape:${locator.shape} table`;
    const table = lines.findIndex(line => line.startsWith(label) && /^(?:\]| "| @)/.test(line.slice(label.length)));
    if (table >= 0) {
      text = lines.slice(table + 1).filter(line => line.startsWith('| ') && line.endsWith(' |'))
        .filter((_, index) => index !== 1)
        .map(line => line.slice(2, -2).split(/(?<!\\)\|/)
          .map(cell => cell.trim().replace(/\\\|/g, '|')).join(' | ')).join('\n');
    }
  }
  // The viewer quotes TSV values and source formulas, not uncached browser calculations.
  // Model-facing tables contain row labels and Markdown
  // separators, which are not part of the selected cells and must not invalidate that quote.
  if (locator.kind === 'xlsx') {
    const formulas = new Map([...text.matchAll(/^([A-Z]+[1-9]\d*): (=[^\n]*)$/gm)]
      .map(match => [match[1], match[2].replace(/  → \(not calculated in file\)$/, '')]));
    let columns: string[] = [];
    const rows: string[] = [];
    for (const line of text.split('\n')) {
      if (!line.startsWith('| ') || !line.endsWith(' |')) continue;
      const cells = line.slice(2, -2).split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
      const row = cells.shift()!;
      if (!row && cells.every(cell => /^[A-Z]+$/.test(cell))) columns = cells;
      else if (/^[1-9]\d*$/.test(row)) rows.push(cells.map((cell, i) => formulas.get(columns[i] + row) ?? cell).join('\t'));
    }
    text = rows.join('\n');
  }
  return {at: formatLocator(locator), text};
});
const blocks = [];
let blockChars = 0;
for (const block of kind === 'docx' ? doc.blocks ?? [] : []) {
  if (blocks.length >= 10000 || blockChars + block.text.length > 200000) break;
  blocks.push(block);
  blockChars += block.text.length;
}
let cells: number | undefined;
if (kind === 'xlsx') {
  cells = 0;
  const archive = await JSZip.loadAsync(bytes);
  for (const name of Object.keys(archive.files)) {
    if (!/^xl\/worksheets\/sheet\d+\.xml$/.test(name)) continue;
    const xml = await archive.files[name].async('string');
    let row = 0, col = 0;
    for (const match of xml.matchAll(/\br=["']([A-Z]{1,3})([1-9]\d{0,6})["']/g)) {
      let column = 0;
      for (const letter of match[1]) column = column * 26 + letter.charCodeAt(0) - 64;
      row = Math.max(row, Number(match[2])); col = Math.max(col, column);
      if (row*col > 100000) break;
    }
    cells += row*col;
    if (cells > 100000) break;
  }
}
const result = {kind, cells, title: doc.title, units: doc.units, outline: doc.outline.slice(0,2000),
    blocks: kind === 'docx' ? blocks : undefined, selections,
    measurement: {wall_ms: performance.now()-started, cpu: process.cpuUsage(), peak_rss_bytes: process.resourceUsage().maxRSS*1024}};
const encoded = JSON.stringify(result);
if (Buffer.byteLength(encoded) > 4*1024*1024) throw Error('output_limit');
await writeFile(output, encoded);
