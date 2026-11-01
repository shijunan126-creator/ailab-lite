import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'vitest';

const modules = new URL('../node_modules/', import.meta.url);
const version = (path) => JSON.parse(readFileSync(new URL(`${path}/package.json`, modules), 'utf8')).version;

// pdf-document.tsx loads the worker from the top-level pdfjs-dist while react-pdf drives its own copy;
// PDF.js refuses to open any file when the two differ, and no CI browser test renders a PDF.
test('the PDF.js worker is the same version react-pdf uses', () => {
  const worker = version('pdfjs-dist');
  const api = existsSync(new URL('react-pdf/node_modules/pdfjs-dist', modules)) ? version('react-pdf/node_modules/pdfjs-dist') : worker;
  assert.equal(api, worker);
});
