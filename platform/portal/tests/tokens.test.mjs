import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'vitest';
import ts from 'typescript';
const src = await readFile(new URL('../lib/tokens.ts', import.meta.url), 'utf8');
const compiled = ts.transpile(src, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 });
const { estimateTokens } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
test('typing estimate: Chinese about 0.7 per character, other text about 4 characters per token', () => {
  assert.equal(estimateTokens(''), 0);
  assert.equal(estimateTokens('你好世界你好世界你好'), 7);
  assert.equal(estimateTokens('hello world, this is!'), 6);
  assert.equal(estimateTokens('做一个 PPT'), Math.ceil(3 * 0.7 + 4 / 4));
});
