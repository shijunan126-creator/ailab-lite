// AILab adapter: pinned upstream parsers, no desktop/viewer or business credentials.
import { readFile, writeFile, mkdir, appendFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { PARSERS } from './src/adapters/parsers.ts';
import { parseLocator } from './src/domain/anchor.ts';
const [source, output, kind] = process.argv.slice(2);
if (!Object.hasOwn(PARSERS, kind)) throw new Error('unsupported');
const assets = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json')) + '/';
const started = performance.now();
const doc = await PARSERS[kind](new Uint8Array(await readFile(source)), 'document.' + kind,
  {cMapUrl: assets + 'cmaps/', standardFontDataUrl: assets + 'standard_fonts/'});
await mkdir(output, {recursive:true});
await writeFile(join(output,'text.txt'),'');
const anchors = kind === 'docx' ? (doc.blocks ?? []).map(b=>b.at)
  : kind === 'xlsx' ? doc.outline.map(o=>o.at)
  : Array.from({length:Math.min(doc.units, kind === 'pdf' ? 3000 : doc.units)}, (_,i)=>(kind==='pdf'?'page:':'slide:')+(i+1));
const units = [];
const visualUnits = [];
let chars = 0, bytes = 0;
for (let unit = 0; unit < anchors.length; unit++) {
  const anchor = anchors[unit];
  const facts = doc.contentInfo?.(anchor) ?? {has_text: true, visual: false};
  if (facts.visual) visualUnits.push({anchor, unit_number:unit+1});
  let cursor = 0, part = 0;
  do {
    const page = doc.read(parseLocator(kind, anchor), cursor);
    const text = facts.has_text ? page.text : "";
    bytes += Buffer.byteLength(text);
    if (bytes > 16*1024*1024 || units.length >= 100000) throw new Error('output_limit');
    const name = String(units.length).padStart(6, '0')+'.txt';
    await writeFile(join(output,name), text);
    await appendFile(join(output,'text.txt'),text+'\n\n');
    units.push({ordinal:units.length, anchor, unit_number:unit+1, part:part++, name,
      quality:facts.has_text?'native_unverified':'no_text', visual:facts.visual,
      chars:text.length, sha256:(await import('node:crypto')).createHash('sha256').update(text).digest('hex')});
    chars += text.length;
    if (page.next !== null && page.next <= cursor) throw new Error('cursor_stalled');
    cursor = page.next;
  } while (cursor !== null);
}
if (doc.hasVisuals && !visualUnits.length) visualUnits.push({anchor:'document', unit_number:1});
await writeFile(join(output,'manifest.json'), JSON.stringify({
  version:1, parser:'zcode-3c5b227-ailab2', kind, title:doc.title, total_units:doc.units,
  status:kind==='pdf' && doc.units>3000?'partial':'ready',
  coverage:anchors, units, chars, visual_units:visualUnits, outline:doc.outline,
  limitations:['Native text only; embedded media, charts and layout require visual review.'],
  measurement:{wall_ms:performance.now()-started, cpu:process.cpuUsage(), peak_rss_bytes:process.resourceUsage().maxRSS*1024}
}));
