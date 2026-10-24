import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const src = await readFile(new URL('../lib/conversation-pages.ts', import.meta.url), 'utf8');
const compiled = ts.transpile(src, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 });
const {mergeTurns, refreshConversation} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const run = (id, status='succeeded') => ({id, created_at:'2026-01-01T00:00:00Z', status});
test('refresh preserves older pages and pagination boundary, but updates a running turn', () => {
  const previous={id:'c', turns:[run('a'),run('b','running')], previews:[], has_more:false};
  const latest={id:'c', turns:[run('b'),run('c')], previews:[], has_more:true};
  const merged=refreshConversation(previous, latest);
  assert.deepEqual(merged.turns.map(r=>r.id), ['a','b','c']);
  assert.equal(merged.turns[1].status,'succeeded');
  assert.equal(merged.has_more,false);
  assert.equal(refreshConversation(previous,{...latest,id:'other'}).turns.length,2);
});
test('equal timestamps have a stable id tie breaker without duplicate turns', () => {
  assert.deepEqual(mergeTurns([run('b')],[run('a'),run('b')]).map(r=>r.id), ['a','b']);
});

test('microsecond timestamps sort chronologically even when whole seconds omit their fraction', () => {
  const turns = mergeTurns([{...run('z'), created_at:'2026-01-01T00:00:00Z'}], [
    {...run('a'), created_at:'2026-01-01T00:00:00.000001Z'},
    {...run('b'), created_at:'2026-01-01T00:00:00.1Z'},
  ]);
  assert.deepEqual(turns.map(r=>r.id), ['z','a','b']);
});

test('a turn the latest page no longer lists was replaced by a retry and disappears', () => {
  const previous={id:'c', turns:[run('a'),run('b')], previews:[], has_more:false};
  const latest={id:'c', turns:[run('a'),run('c')], previews:[], has_more:false};
  assert.deepEqual(refreshConversation(previous, latest).turns.map(r=>r.id), ['a','c']);
});

test('a refresh replaces the complete path preview manifest, discarding abandoned branch files', () => {
  const previous={id:'c', turns:[run('a'),run('b')], previews:[{filename:'abandoned.pptx'}], has_more:false};
  const latest={id:'c', turns:[run('a'),run('c')], previews:[], has_more:false};
  assert.deepEqual(refreshConversation(previous, latest).previews, []);
});
