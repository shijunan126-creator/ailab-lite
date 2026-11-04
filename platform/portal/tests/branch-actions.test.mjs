import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'vitest';
import ts from 'typescript';

// Execute the actual component handlers with controlled network completion order.
const source = await readFile(new URL('../components/chat/chat.tsx', import.meta.url), 'utf8');
const functions = [
  source.slice(source.indexOf('  function beginBranchChange()'), source.indexOf('  /** A new version of a turn')),
  source.slice(source.indexOf('  async function switchTo('), source.indexOf('  /** Continue from a turn')),
].join('\n');
const compiled = ts.transpile(functions, { target: ts.ScriptTarget.ES2022 });
function harness() {
  const calls = [];
  let complete;
  let displayed = { id: 'c', branch_revision: 3, turns: [] };
  const detail = displayed;
  const abort = new AbortController();
  const branchMutation = { current: false };
  const loadSeq = { current: 0 };
  let refreshes = 0;
  const noop = () => {};
  const api = (_path, request) => { calls.push(JSON.parse(request.body)); return new Promise(resolve => { complete = resolve; }); };
  const vars = { id:'c', detail, branchMutation, visibleRevision:{current:3}, loadSeq, actionController:{current:abort}, opened:{current:new Set()},
    detailController:{current:null}, olderController:{current:null}, streamController:{current:null},
    setBranchChanging:noop, markDetailWrite:noop, setLoadingOlder:noop, setStreaming:noop,
    loadDetail:()=>{ refreshes++; }, setLivePreviews:noop, setLibraryPreviews:noop, setPaneSources:noop,
    setSheetSources:noop, setSourcesOn:noop, setError:noop, setDetail:update=>{ displayed=update(displayed); }, api };
  const switchTo = new Function(...Object.keys(vars), `${compiled}; return switchTo;`)(...Object.values(vars));
  return { calls, branchMutation, switchTo, finish:page=>complete(page), displayed:()=>displayed, refreshes:()=>refreshes };
}

test('different turns cannot start competing version POSTs or overwrite the selected path', async () => {
  const h = harness();
  const first = h.switchTo('B');
  await h.switchTo('A');
  assert.deepEqual(h.calls, [{run_id:'B',expected_branch_revision:3}]);
  assert.equal(h.branchMutation.current,true);
  h.finish({id:'c',branch_revision:4,active_run_id:'B',turns:[]});
  await first;
  assert.equal(h.displayed().active_run_id,'B');
  assert.equal(h.branchMutation.current,false);
  assert.equal(h.refreshes(),1);
});
