import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { test } from 'vitest';

const dir = new URL('../styles/', import.meta.url);
const css = (await Promise.all((await readdir(dir)).filter((f) => f.endsWith('.css')).map((f) => readFile(new URL(f, dir), 'utf8')))).join('\n');

test('every visually hidden helper has a solid 1px box', () => {
  for (const [, selector, body] of css.matchAll(/(\.[\w-]+)\s*\{([^}]*)\}/g)) {
    if (!/position:\s*absolute/.test(body) || !/clip-path:\s*inset\(50%\)/.test(body)) continue;
    assert.match(body, /width:\s*1px/, `${selector} must keep its hidden box to 1px`);
    assert.match(body, /height:\s*1px/, `${selector} must keep its hidden box to 1px`);
  }
});
