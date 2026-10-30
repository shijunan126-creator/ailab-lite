import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from 'vitest';
import { t } from './node-test-compat.mjs';
import ts from "typescript";
import config from "../next.config.mjs";

const require = createRequire(import.meta.url);
const next = require("next/server");
const { getRelativeURL } = require("next/dist/shared/lib/router/utils/relativize-url");
const compiled = ts.transpile(await readFile(new URL("../proxy.ts", import.meta.url), "utf8"), {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled)(require, module, module.exports);

test("anonymous login rewrites stay internal on each request origin", () => {
  // Match Next's build-time flag and router origin comparison: an external
  // rewrite makes a needless HTTP hop and can fail before the form is rendered.
  const flag = "__NEXT_NO_MIDDLEWARE_URL_NORMALIZE";
  const previous = process.env[flag];
  t.after(() => { if (previous === undefined) delete process.env[flag]; else process.env[flag] = previous; });
  process.env[flag] = config.skipProxyUrlNormalize ? "true" : "";
  for (const origin of ["http://127.0.0.1:13000", "http://[::1]:13000", "https://portal.example"]) {
    const url = origin + "/files?view=recent";
    const response = module.exports.proxy(new next.NextRequest(url));
    assert.equal(getRelativeURL(response.headers.get("x-middleware-rewrite"), url), "/login");
    assert.equal(response.headers.get("location"), null);
  }
});

test("authenticated requests continue without a login rewrite", () => {
  const response = module.exports.proxy(new next.NextRequest("http://127.0.0.1:13000/files", {
    headers: { cookie: "ailab_token=test-session" },
  }));
  assert.equal(response.headers.get("x-middleware-next"), "1");
  assert.equal(response.headers.get("x-middleware-rewrite"), null);
});
