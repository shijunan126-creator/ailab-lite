import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const css = await readFile(new URL('../app/globals.css', import.meta.url), 'utf8');

test('every animation the styles use has its keyframes', () => {
  const defined = new Set([...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]));
  const keywords = new Set(['none', 'infinite', 'linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out', 'reverse', 'alternate', 'both', 'forwards', 'backwards']);
  const used = new Set();
  for (const [, value] of css.matchAll(/animation(?:-name)?\s*:\s*([^;}]+)/g)) {
    for (const part of value.split(',')) {
      const name = part.trim().split(/\s+/).find((w) => /^[a-z][\w-]*$/i.test(w) && !keywords.has(w) && !/^steps|^cubic/.test(w));
      if (name) used.add(name);
    }
  }
  const missing = [...used].filter((n) => !defined.has(n));
  assert.deepEqual(missing, [], `animations without @keyframes: ${missing.join(', ')}`);
});
