// AILab adapter: pinned upstream parsers, no desktop/viewer or business credentials.
import { readFile, writeFile, mkdir, appendFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { PARSERS } from './src/adapters/parsers.ts';
import { formatLocator, formatSheet, parseLocator } from './src/domain/anchor.ts';

async function main() {
  const [source, output, kind, profile = 'legacy', scopePath] = process.argv.slice(2);
  if (!['legacy', 'chat', 'chat-full', 'scope'].includes(profile)) throw new Error('unsupported_profile');
  const bounded = profile !== 'legacy';
  if (!Object.hasOwn(PARSERS, kind)) throw new Error('unsupported');
  const scope = scopePath ? JSON.parse(await readFile(scopePath, 'utf8')) : null;
  if ((profile === 'scope') !== !!scope) throw new Error('document_selector_invalid');
  if (scope && !['pages','word','sheet'].includes(scope.kind)) throw new Error('document_selector_invalid');
  if (scope?.kind === 'pages' && (!['pdf','pptx'].includes(kind) || !Array.isArray(scope.pages)
    || scope.pages.length < 1 || scope.pages.length > 20 || scope.pages.some(n => !Number.isSafeInteger(n) || n < 1)
    || new Set(scope.pages).size !== scope.pages.length)) throw new Error('document_selector_invalid');
  if (scope?.kind === 'word' && kind !== 'docx') throw new Error('document_selector_invalid');
  if (scope?.kind === 'sheet' && kind !== 'xlsx') throw new Error('document_selector_invalid');
  const assets = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json')) + '/';
  const started = performance.now();
  const input = await readFile(source);
  const doc = await PARSERS[kind](new Uint8Array(input.buffer, input.byteOffset, input.byteLength), 'document.' + kind,
    {cMapUrl: assets + 'cmaps/', standardFontDataUrl: assets + 'standard_fonts/',
     ...(bounded ? {consumeBytes:true, maxNativeChars:200000, maxNativeBlocks:10000,
       nativeProbe:profile==='chat', inspectVisuals:profile==='chat'} : {}),
     ...(scope?.kind==='pages' && kind==='pdf' ? {indexedPages:scope.pages} : {})});
  if (bounded && (doc.nonemptyCells ?? 0) > 200000) throw new Error('document_cells_limit');
  if (bounded && kind==='docx' && (doc.blocks?.length ?? 0)>10000) throw new Error('document_structure_limit');
  await mkdir(output, {recursive:true});
  await writeFile(join(output,'text.txt'),'');
  let anchors = scope?.kind === 'pages' ? scope.pages.map(n => (kind==='pdf'?'page:':'slide:')+n)
    : scope?.kind === 'word' ? [scope.anchor]
    : scope?.kind === 'sheet' ? [formatSheet(scope.sheet)+(scope.range?'!'+scope.range:'')]
    : kind === 'docx' ? (doc.blocks ?? []).map(b=>b.at)
    : kind === 'xlsx' ? doc.outline.map(o=>o.at)
    : kind === 'pdf' && bounded ? (doc.indexedPages ?? []).map(n=>'page:'+n)
    : Array.from({length:Math.min(doc.units, kind === 'pdf' ? 3000 : doc.units)}, (_,i)=>(kind==='pdf'?'page:':'slide:')+(i+1));
  if (scope) {
    try { anchors = anchors.map(anchor => formatLocator(doc.check(parseLocator(kind, anchor)))); }
    catch { throw new Error('document_selector_invalid'); }
  }
  const units = [], visualUnits = [];
  let chars = 0, bytes = 0, byteOffset = 0;
  for (let unit = 0; unit < anchors.length; unit++) {
    const anchor = anchors[unit];
    const facts = doc.contentInfo?.(anchor) ?? {has_text:true, visual:false};
    const unitNumber = ['pdf','pptx'].includes(kind) ? Number(anchor.split(':')[1]) : unit+1;
    const visual = bounded ? facts.native_visual ?? facts.visual : facts.visual;
    if (visual) visualUnits.push({anchor, unit_number:unitNumber});
    let cursor = 0, part = 0;
    do {
      const page = doc.read(parseLocator(kind, anchor), cursor);
      const text = facts.has_text ? page.text : '';
      bytes += Buffer.byteLength(text);
      if (bounded && chars + text.length > 200000) throw new Error('document_text_limit');
      if (bounded && units.length >= 10000) throw new Error('document_structure_limit');
      if (bytes > 16*1024*1024 || units.length >= 100000) throw new Error('output_limit');
      const name = bounded ? 'text.txt' : String(units.length).padStart(6,'0')+'.txt';
      if (!bounded) await writeFile(join(output,name), text);
      await appendFile(join(output,'text.txt'),text+'\n\n');
      units.push({ordinal:units.length, anchor, unit_number:unitNumber, part:part++, name,
        ...(bounded ? {byte_offset:byteOffset,byte_size:Buffer.byteLength(text)} : {}),
        quality:facts.has_text?'native_unverified':'no_text', visual,
        chars:text.length, sha256:(await import('node:crypto')).createHash('sha256').update(text).digest('hex')});
      chars += text.length;
      byteOffset += Buffer.byteLength(text)+2;
      if (page.next !== null && page.next <= cursor) throw new Error('cursor_stalled');
      cursor = page.next;
    } while (cursor !== null);
  }
  // Office adapters still parse the complete package. Inspect every source
  // block under the same caps before publishing a small selected projection.
  let sourceChars = chars, sourceBlocks = units.length;
  if (scope && kind !== 'pdf') {
    sourceChars = 0; sourceBlocks = 0;
    const sourceAnchors = kind === 'docx' ? (doc.blocks ?? []).map(b => b.at)
      : kind === 'xlsx' ? doc.outline.map(o => o.at)
      : Array.from({length:doc.units}, (_,i) => 'slide:'+(i+1));
    for (const anchor of sourceAnchors) {
      let offset = 0;
      do {
        const page = doc.read(parseLocator(kind, anchor), offset);
        sourceChars += page.text.length; sourceBlocks++;
        if (sourceChars > 200000) throw new Error('document_text_limit');
        if (sourceBlocks > 10000) throw new Error('document_structure_limit');
        if (page.next !== null && page.next <= offset) throw new Error('cursor_stalled');
        offset = page.next;
      } while (offset !== null);
    }
  }
  if (doc.hasVisuals && !visualUnits.length) visualUnits.push({anchor:'document', unit_number:1});
  const complete = kind==='pdf' && bounded ? doc.coverageComplete === true : kind==='pdf' ? doc.units<=3000 : true;
  await writeFile(join(output,'manifest.json'), JSON.stringify({
    version:bounded?2:1, parser:bounded?'office-a60ba6a-ailab5':'office-a60ba6a-ailab3', kind, title:doc.title, total_units:doc.units,
    status:complete?'ready':'partial', coverage:anchors, units, chars, text_bytes:byteOffset, cells:doc.nonemptyCells ?? null,
    source_chars:sourceChars, source_blocks:sourceBlocks,
    visual_units:visualUnits, outline:doc.outline, ...(bounded ? {scope, visual_status:doc.visualStatus ?? (visualUnits.length?'present':'none')} : {}),
    limitations:['Native text only; embedded media, charts and layout require visual review.',
      ...(scope && kind!=='pdf' ? ['Office source parsing still loads the whole package before selecting this range.'] : []),
      ...(!complete ? ['Unprepared pages remain unknown; native page ranges are prepared explicitly.'] : [])],
    measurement:{wall_ms:performance.now()-started, cpu:process.cpuUsage(), peak_rss_bytes:process.resourceUsage().maxRSS*1024}
  }));
}
try { await main(); }
catch (error) {
  const reason = error instanceof Error ? error.message : '';
  const allowed = ['document_text_limit','document_cells_limit','document_structure_limit','output_limit','unsupported','unsupported_profile',
    'document_selector_invalid','invalid_page_selection','document_range_not_prepared'];
  console.error(JSON.stringify({error:allowed.includes(reason)?reason:'document_parse_failed'}));
  process.exitCode = 2;
}
