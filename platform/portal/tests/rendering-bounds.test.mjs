import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
async function module(path) {
 const src=await readFile(new URL(path,import.meta.url),'utf8');
 return import(`data:text/javascript;base64,${Buffer.from(ts.transpile(src,{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022})).toString('base64')}`);
}
const {markdownChunks}=await module('../lib/markdown-chunks.ts');
const {cacheConversation,cachedConversation}=await module('../lib/conversation-cache.ts');
const {reduceEvent}=await module('../lib/work-process.ts');

test('400k characters remain byte-for-byte intact across deferred Markdown chunks',()=>{
 const text='开头\n\n'+('**分析**\n内容。'.repeat(30)+'\n\n').repeat(1600)+'结束';
 const chunks=markdownChunks(text);
 assert.equal(chunks.join(''),text);
 assert.ok(chunks.length>60);
 assert.ok(Math.max(...chunks.map(x=>x.length))<6500);
 const code='```python\n'+('print(1)\n'.repeat(2000))+'```\n\n结尾';
 assert.equal(markdownChunks(code).join(''),code);
 assert.equal(markdownChunks(code)[0],code.slice(0,-2));
 const lines=('一行内容。\n').repeat(100000);
 const lineChunks=markdownChunks(lines);
 assert.equal(lineChunks.join(''),lines);
 assert.ok(Math.max(...lineChunks.map(x=>x.length))<12100);
});
test('streaming a long thought preserves earlier references and all 400k characters without mutating state',()=>{
 const first={kind:'text',step:0,text:'earlier'};
 let items=[first];
 for(let i=0;i<400;i++) items=reduceEvent(items,{type:'agent.thinking',payload:{step:1,text:'思'.repeat(1000)}});
 const before=items;
 const next=reduceEvent(items,{type:'agent.thinking',payload:{step:1,text:'尾'}});
 assert.equal(next[0],before[0]);
 assert.equal(before[1].text.length,400000);
 assert.equal(next[1].text,'思'.repeat(400000)+'尾');
});
test('cache retains one page and cursor semantics and evicts by aggregate size',()=>{
 const run=i=>({id:String(i),prompt:'p',summary:'answer'});
 const detail={id:'large',turns:Array.from({length:500},(_,i)=>run(i)),has_more:false,previews:[]};
 cacheConversation(detail);
 assert.equal(cachedConversation('large').turns.length,20);
 assert.equal(cachedConversation('large').turns[0].id,'480');
 assert.equal(cachedConversation('large').has_more,true);
 assert.equal(detail.turns.length,500);
 cacheConversation({id:'oversize',turns:[{summary:'x'.repeat(1000001)}]});
 assert.equal(cachedConversation('oversize'),null);
 for(let i=0;i<8;i++) cacheConversation({id:`c${i}`,turns:[{summary:'x'.repeat(250000)}]});
 assert.equal(cachedConversation('c0'),null);
 assert.ok(cachedConversation('c7'));
});
