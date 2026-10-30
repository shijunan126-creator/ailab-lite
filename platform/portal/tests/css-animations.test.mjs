import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { test } from 'vitest';

// every Portal stylesheet: an animation in one file may use keyframes from another
const dir = new URL('../styles/', import.meta.url);
const css = (await Promise.all((await readdir(dir)).filter((f) => f.endsWith('.css')).map((f) => readFile(new URL(f, dir), 'utf8')))).join('\n');

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
