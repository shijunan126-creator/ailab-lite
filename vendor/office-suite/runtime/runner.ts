// AILab adapter: pinned upstream parsers, no desktop/viewer or business credentials.
import { readFile, writeFile, mkdir, appendFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { PARSERS } from './src/adapters/parsers.ts';
import { parseLocator } from './src/domain/anchor.ts';
async function main() {
  const [source, output, kind, profile = "legacy"] = process.argv.slice(2);
  if (!["legacy", "chat"].includes(profile)) throw new Error('unsupported_profile');
  const bounded = profile === "chat";
  if (!Object.hasOwn(PARSERS, kind)) throw new Error('unsupported');
  const assets = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json')) + '/';
  const started = performance.now();
  const doc = await PARSERS[kind](new Uint8Array(await readFile(source)), 'document.' + kind,
    {cMapUrl: assets + 'cmaps/', standardFontDataUrl: assets + 'standard_fonts/'});
  if (bounded && (doc.nonemptyCells ?? 0) > 200000) throw new Error('document_cells_limit');
  await mkdir(output, {recursive:true});
  await writeFile(join(output,'text.txt'),'');
  const anchors = kind === 'docx' ? (doc.blocks ?? []).map(b=>b.at)
    : kind === 'xlsx' ? doc.outline.map(o=>o.at)
    : Array.from({length:Math.min(doc.units, kind === 'pdf' ? 3000 : doc.units)}, (_,i)=>(kind==='pdf'?'page:':'slide:')+(i+1));
  const units = [];
  const visualUnits = [];
  let chars = 0, bytes = 0, byteOffset = 0;
  for (let unit = 0; unit < anchors.length; unit++) {
    const anchor = anchors[unit];
    const facts = doc.contentInfo?.(anchor) ?? {has_text: true, visual: false};
    if (facts.visual) visualUnits.push({anchor, unit_number:unit+1});
    let cursor = 0, part = 0;
    do {
      const page = doc.read(parseLocator(kind, anchor), cursor);
      const text = facts.has_text ? page.text : "";
      bytes += Buffer.byteLength(text);
      if (bounded && chars + text.length > 200000) throw new Error('document_text_limit');
      if (bounded && units.length >= 10000) throw new Error('document_structure_limit');
      if (bytes > 16*1024*1024 || units.length >= 100000) throw new Error('output_limit');
      const name = bounded ? 'text.txt' : String(units.length).padStart(6, '0')+'.txt';
      if (!bounded) await writeFile(join(output,name), text);
      await appendFile(join(output,'text.txt'),text+'\n\n');
      units.push({ordinal:units.length, anchor, unit_number:unit+1, part:part++, name,
        ...(bounded ? {byte_offset:byteOffset,byte_size:Buffer.byteLength(text)} : {}),
        quality:facts.has_text?'native_unverified':'no_text', visual:facts.visual,
        chars:text.length, sha256:(await import('node:crypto')).createHash('sha256').update(text).digest('hex')});
      chars += text.length;
      byteOffset += Buffer.byteLength(text) + 2;
      if (page.next !== null && page.next <= cursor) throw new Error('cursor_stalled');
      cursor = page.next;
    } while (cursor !== null);
  }
  if (doc.hasVisuals && !visualUnits.length) visualUnits.push({anchor:'document', unit_number:1});
  await writeFile(join(output,'manifest.json'), JSON.stringify({
    version:bounded?2:1, parser:bounded?'office-a60ba6a-ailab4':'office-a60ba6a-ailab3', kind, title:doc.title, total_units:doc.units,
    status:kind==='pdf' && doc.units>3000?'partial':'ready',
    coverage:anchors, units, chars, text_bytes:byteOffset, cells:doc.nonemptyCells ?? null, visual_units:visualUnits, outline:doc.outline,
    limitations:['Native text only; embedded media, charts and layout require visual review.'],
    measurement:{wall_ms:performance.now()-started, cpu:process.cpuUsage(), peak_rss_bytes:process.resourceUsage().maxRSS*1024}
  }));

}
try { await main(); }
catch (error) {
  const reason = error instanceof Error ? error.message : '';
  const allowed = ['document_text_limit','document_cells_limit','document_structure_limit','output_limit','unsupported','unsupported_profile'];
  console.error(JSON.stringify({error:allowed.includes(reason)?reason:'document_parse_failed'}));
  process.exitCode = 2;
}
