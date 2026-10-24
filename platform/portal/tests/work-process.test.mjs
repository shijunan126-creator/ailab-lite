import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../lib/work-process.ts', import.meta.url), 'utf8');
const compiled = ts.transpile(source, {module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022});
const {reduceEvent,workHeading} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const event=(type,payload)=>({type,payload});

test('public narrative, plan updates and tools retain their order without raw reasoning',()=>{
 let items=[];
 items=reduceEvent(items,event('agent.thinking',{step:0,text:'PRIVATE_REASONING',seconds:3}));
 items=reduceEvent(items,event('agent.message',{step:0,content:'先检查环境。'}));
 items=reduceEvent(items,event('plan.updated',{steps:[{id:'a',title:'检查环境',status:'running'}]}));
 items=reduceEvent(items,event('agent.tool_call',{step:1,name:'exec',arguments:{cmd:'python -V'}}));
 const before=JSON.stringify(items);
 const changed=reduceEvent(items,event('agent.tool_result',{step:1,name:'exec',ok:true,stdout:'Python 3.12'}));
 assert.equal(JSON.stringify(items),before,'updates must not mutate earlier state');
 items=reduceEvent(changed,event('plan.updated',{steps:[{id:'a',title:'检查环境',status:'done'}]}));
 assert.deepEqual(items.map(i=>i.kind),['thinking','text','plan','steps','plan']);
 assert.equal(items[2].steps[0].status,'running');
 assert.equal(items[4].steps[0].status,'done');
 assert.ok(!JSON.stringify(items).includes('PRIVATE_REASONING'));
 assert.equal(items[3].steps[0].status,'ok');
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
