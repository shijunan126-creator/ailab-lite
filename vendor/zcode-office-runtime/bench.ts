import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname} from 'node:path';
import {PARSERS} from './src/adapters/parsers.ts';
import {parseLocator} from './src/domain/anchor.ts';
const [file,kind]=process.argv.slice(2);
const assets=dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'))+'/';
const bytes=new Uint8Array(await readFile(file));const results=[];
for(let i=0;i<2;i++){
  const start=performance.now(),cpu=process.cpuUsage();
  const doc=await PARSERS[kind](bytes,'document.'+kind,{cMapUrl:assets+'cmaps/',standardFontDataUrl:assets+'standard_fonts/'});
  const anchors=kind==='docx'?(doc.blocks??[]).map(b=>b.at):kind==='xlsx'?doc.outline.map(o=>o.at):Array.from({length:doc.units},(_,n)=>(kind==='pdf'?'page:':'slide:')+(n+1));
  let chars=0;
  for(const at of anchors){let cursor=0;do{const p=doc.read(parseLocator(kind,at),cursor);chars+=p.text.length;cursor=p.next;}while(cursor!==null);}
  results.push({wall_ms:performance.now()-start,cpu:process.cpuUsage(cpu),chars,units:doc.units});
}
console.log(JSON.stringify({results,peak_rss_bytes:process.resourceUsage().maxRSS*1024}));
