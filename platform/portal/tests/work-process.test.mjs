import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../lib/work-process.ts', import.meta.url), 'utf8');
const compiled = ts.transpile(source, {module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022});
const mod = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const {reduceEvent,workHeading,splitTurn,thoughtOutline,turnSources,siteOf} = mod;
const event=(type,payload)=>({type,payload});

test('reasoning, narrative, plan updates and tools retain their order; reasoning text streams into its block',()=>{
 let items=[];
 items=reduceEvent(items,event('agent.thinking',{step:0,text:'**检查环境**\n先看',seconds:1}));
 items=reduceEvent(items,event('agent.thinking',{step:0,text:'看 Python 版本。',seconds:3}));
 items=reduceEvent(items,event('agent.message',{step:0,content:'先检查环境。'}));
 items=reduceEvent(items,event('plan.updated',{steps:[{id:'a',title:'检查环境',status:'running'}]}));
 items=reduceEvent(items,event('agent.tool_call',{step:1,name:'exec',arguments:{cmd:'python -V'}}));
 const before=JSON.stringify(items);
 const changed=reduceEvent(items,event('agent.tool_result',{step:1,name:'exec',ok:true,stdout:'Python 3.12'}));
 assert.equal(JSON.stringify(items),before,'updates must not mutate earlier state');
 items=reduceEvent(changed,event('plan.updated',{steps:[{id:'a',title:'检查环境',status:'done'}]}));
 assert.deepEqual(items.map(i=>i.kind),['thinking','text','plan','steps','plan']);
 assert.equal(items[0].text,'**检查环境**\n先看看 Python 版本。');
 assert.equal(items[0].seconds,3);
 assert.equal(items[0].done,true);
 assert.equal(items[2].steps[0].status,'running');
 assert.equal(items[4].steps[0].status,'done');
 assert.equal(items[3].steps[0].status,'ok');
});
test('a new model step starts a new reasoning block',()=>{
 let items=reduceEvent([],event('agent.thinking',{step:0,text:'a',seconds:1}));
 items=reduceEvent(items,event('agent.tool_call',{step:0,name:'web_search',arguments:{query:'q'}}));
 items=reduceEvent(items,event('agent.thinking',{step:1,text:'b',seconds:1}));
 assert.deepEqual(items.filter(i=>i.kind==='thinking').map(i=>i.text),['a','b']);
});
test('late tool result cannot finish a later call with the same name',()=>{
 let items=reduceEvent([],event('agent.tool_call',{step:2,name:'exec',arguments:{}}));
 items=reduceEvent(items,event('agent.tool_result',{step:1,name:'exec',ok:true}));
 assert.equal(items[0].steps[0].status,'running');
});
test('status header uses the persisted clock, never browser wall time or a legacy estimate',()=>{
 for (const [status,label] of Object.entries({queued:'等待开始',running:'正在工作中…',waiting_user:'等待你回答',interrupted:'已暂停'}))
  assert.equal(workHeading({status,work_ms:125000}),label);
 assert.equal(workHeading({status:'queued',started_at:'2026-01-01'}),'等待继续');
 assert.equal(workHeading({status:'succeeded',work_ms:125750}),'工作总时长：2分5秒');
 assert.equal(workHeading({status:'failed',work_ms:0}),'执行失败 · 工作总时长：0分0秒');
 assert.equal(workHeading({status:'canceled',work_ms:125750}),'已取消 · 工作总时长：2分5秒');
 assert.equal(workHeading({status:'succeeded',started_at:'2000-01-01',finished_at:'2026-01-01',work_ms:null}),'工作已完成');
});
test('chat mode only thinks: its heading never reports work time',()=>{
 const chat=(status,work_ms)=>workHeading({status,work_ms,options:{mode:'chat'}});
 assert.equal(chat('running',3000),'正在思考…');
 assert.equal(chat('succeeded',12400),'已思考 12秒');
 assert.equal(chat('succeeded',300),'已思考 1秒');
 assert.equal(chat('succeeded',125750),'已思考 2分5秒');
 assert.equal(chat('succeeded',null),'已思考');
 assert.equal(chat('failed',5000),'回答失败');
 assert.equal(chat('canceled',5000),'已取消');
 for(const status of ['running','succeeded','failed','canceled']) assert.ok(!chat(status,5000).includes('工作'));
});
test('network and skill tools read as their actions, with the source they used',()=>{
 const label=(name,args)=>reduceEvent([],event('agent.tool_call',{step:1,name,arguments:args}))[0].steps[0];
 assert.deepEqual([label('fetch_repo',{repo:'o/r',ref:'v1'}).label,label('fetch_repo',{repo:'o/r',ref:'v1'}).detail],['获取仓库','o/r@v1']);
 assert.equal(label('fetch_packages',{packages:['rich','six']}).detail,'rich six');
 assert.equal(label('download_url',{url:'https://x.test/a.png'}).label,'下载文件');
 assert.equal(label('save_skill',{name:'s',mode:'update'}).label,'更新个人技能');
 assert.equal(label('save_skill',{name:'s',mode:'create'}).label,'保存个人技能');
});

test('the answer is the text after the last action; trailing platform notes do not pull it into the process',()=>{
 let items=reduceEvent([],event('agent.tool_call',{step:0,name:'web_search',arguments:{query:'q'}}));
 items=reduceEvent(items,event('agent.tool_result',{step:0,name:'web_search',ok:true,sources:[{title:'A',url:'https://a.test/x',site:'a.test'},{title:'bad',url:'javascript:alert(1)'}]}));
 items=reduceEvent(items,event('agent.delta',{step:1,text:'答案'}));
 let split=splitTurn(items);
 assert.equal(split.answer,'答案');
 assert.deepEqual(split.process.map(i=>i.kind),['steps']);
 items=reduceEvent(items,event('preview.updated',{filename:'a.pptx'}));
 split=splitTurn(items);
 assert.equal(split.answer,'答案');
 assert.ok(!split.process.some(i=>i.kind==='text'));
 assert.deepEqual(turnSources(items).map(s=>s.url),['https://a.test/x']);
 // narration followed by another tool call belongs to the process
 items=reduceEvent(items,event('agent.tool_call',{step:1,name:'exec',arguments:{cmd:'ls'}}));
 split=splitTurn(items);
 assert.equal(split.answer,'');
 assert.ok(split.process.some(i=>i.kind==='text'));
 assert.equal(splitTurn(null).answer,'');
});
test('reasoning outline: bold lines name the parts, else the first sentence',()=>{
 assert.deepEqual(thoughtOutline('**读题**\n用户想要表格。\n\n**设计列**\n地区、月份。').map(p=>p.title),['读题','设计列']);
 assert.equal(thoughtOutline('**读题**\n用户想要表格。')[0].body,'用户想要表格。');
 const plain=thoughtOutline('用户问的是天气。我需要先搜索今天的预报，然后总结。');
 assert.equal(plain.length,1);
 assert.equal(plain[0].title,'用户问的是天气');
 assert.equal(plain[0].body,'我需要先搜索今天的预报，然后总结。');
 assert.deepEqual(thoughtOutline(''),[]);
 assert.equal(siteOf('https://www.example.com/a'),'example.com');
});

test('a message added while the answer runs is kept once and ends the text before it as process, not answer',()=>{
 let items=[];
 items=reduceEvent(items,event('agent.delta',{step:0,text:'先看第一部分。'}));
 items=reduceEvent(items,event('agent.steered',{queued_id:'q1',prompt:'顺便对比去年'}));
 items=reduceEvent(items,event('agent.steered',{queued_id:'q1',prompt:'顺便对比去年'}));
 items=reduceEvent(items,event('agent.delta',{step:1,text:'已加入去年的对比。'}));
 assert.deepEqual(items.filter((it)=>it.kind==='steer'),[{kind:'steer',id:'q1',prompt:'顺便对比去年'}]);
 const {process,answer}=splitTurn(items);
 assert.equal(answer,'已加入去年的对比。');
 assert.ok(process.some((it)=>it.kind==='text'&&it.text.includes('先看第一部分')));
});
test('sources the answer links to come first in its order; the rest are more',()=>{
 const {splitSources,urlKey}=mod;
 const a={title:'A',url:'https://a.com/x/',site:'a.com'},b={title:'B',url:'https://b.com/y',site:'b.com'},c={title:'C',url:'https://c.com/',site:'c.com'};
 const {cited,more}=splitSources([a,b,c],'事实一 [B站](https://b.com/y#top)。事实二 [A](https://a.com/x) 再次 [B](https://b.com/y)');
 assert.deepEqual(cited.map(s=>s.title),['B','A']);
 assert.deepEqual(more.map(s=>s.title),['C']);
 assert.equal(urlKey('https://a.com/x/#h'),urlKey('https://a.com/x'));
 assert.deepEqual(splitSources([a],'没有链接').cited,[]);
});
test('budget events, run-scope compaction and background commands read plainly',()=>{
 let items=[];
 items=reduceEvent(items,event('agent.tool_call',{step:1,name:'exec',arguments:{cmd:'make',background:true}}));
 items=reduceEvent(items,event('agent.tool_call',{step:2,name:'exec_wait',arguments:{handle:'a'}}));
 items=reduceEvent(items,event('agent.budget',{kind:'extended',minutes:60.2,extension_minutes:30}));
 items=reduceEvent(items,event('agent.budget',{kind:'paused',minutes:240,reason:'已工作4小时，已暂停，可继续'}));
 items=reduceEvent(items,event('agent.context_compacted',{scope:'run',messages:40}));
 const steps=items.flatMap(i=>i.kind==='steps'?i.steps:[]);
 assert.deepEqual(steps.map(s=>s.label),['后台运行命令','查看后台命令','有进展，继续工作','已暂停','已压缩本轮较早的步骤']);
 assert.equal(steps[3].detail,'已工作4小时，已暂停，可继续');
});
