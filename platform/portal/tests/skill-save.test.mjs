import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import { test } from 'vitest';
import ts from 'typescript';
const source = await readFile(new URL('../components/skills/skill-save.ts', import.meta.url), 'utf8');
const compiled = ts.transpile(source, {module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022});
const {waitForScan,isLocked,scanProblem,saveError,frontmatterName,SCANNING} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('waits until the new version leaves pending and survives a transient failure',async()=>{
 const answers=[new Error('502'),{id:'f',status:'pending'},{id:'f',status:'ready',meta:{skill:{name:'a'}}}];
 const seen=[];
 const file=await waitForScan('f',async(id)=>{seen.push(id);const a=answers.shift();if(a instanceof Error)throw a;return a;},{signal:new AbortController().signal,intervalMs:1});
 assert.equal(file.status,'ready');
 assert.deepEqual(seen,['f','f','f']);
});

test('gives up at the deadline instead of polling forever',async()=>{
 let t=0,calls=0;
 const file=await waitForScan('f',async()=>{calls++;t+=400;return {id:'f',status:'pending'};},{signal:new AbortController().signal,deadlineMs:1000,intervalMs:1,now:()=>t});
 assert.equal(file,null);
 assert.equal(calls,3);
});

test('unmounting aborts the wait and no further request is made',async()=>{
 const ctl=new AbortController();
 let calls=0;
 const pending=waitForScan('f',async()=>{calls++;return {id:'f',status:'pending'};},{signal:ctl.signal,intervalMs:50});
 ctl.abort(new Error('gone'));
 await assert.rejects(pending,/gone/);
 assert.equal(calls,0);
});

test('a head being scanned cannot be edited or replaced again',()=>{
 assert.equal(isLocked({file_id:'a',error:SCANNING},new Set()),true);
 assert.equal(isLocked({file_id:'a',error:null},new Set(['a'])),true);
 assert.equal(isLocked({file_id:'a',error:null},new Set(['b'])),false);
 assert.equal(isLocked({file_id:'a',error:'未通过安全扫描'},new Set()),false);
});

test('scan outcomes and save failures read as words',()=>{
 assert.equal(scanProblem({id:'f',status:'ready',meta:{skill:{name:'a'}}}),'');
 assert.equal(scanProblem({id:'f',status:'rejected'}),'没有通过安全扫描');
 assert.equal(scanProblem({id:'f',status:'ready',meta:{skill_error:'缺少 description'}}),'缺少 description');
 assert.match(scanProblem({id:'f',status:'ready',meta:{}}),/不是技能文件/);
 assert.equal(saveError(422,JSON.stringify({detail:'技能已更新，请重新读取后再保存'})),'技能已更新，请重新读取后再保存');
 assert.match(saveError(422,''),/别处修改/);
 assert.match(saveError(500,'oops'),/500/);
});

test('front matter name decides which skill is saved',()=>{
 assert.equal(frontmatterName('---\nname: weekly-report\ndescription: x\n---\n# hi'),'weekly-report');
 assert.equal(frontmatterName('﻿---\r\ntitle: t\r\nname: "quoted"\r\n---\r\n'),'quoted');
 assert.equal(frontmatterName('# no front matter\nname: nope'),'');
});
