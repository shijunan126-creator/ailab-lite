import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const next = require("next/server");
const serverSource = await readFile(new URL("../lib/server.ts", import.meta.url), "utf8");
function compile(source, dependencies) {
  const module = { exports: {} };
  const compiled = ts.transpile(source, { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 });
  new Function("require", "module", "exports", compiled)((name) => dependencies[name], module, module.exports);
  return module.exports;
}
const server = compile(serverSource, { "next/server": next });
const dependencies = { "next/server": next, "../../../../../lib/server": server };
const start = compile(await readFile(new URL("../app/api/auth/oidc/start/route.ts", import.meta.url), "utf8"), dependencies);
const callback = compile(await readFile(new URL("../app/api/auth/oidc/callback/route.ts", import.meta.url), "utf8"), dependencies);
const proxy = compile(await readFile(new URL("../app/api/v1/[...path]/route.ts", import.meta.url), "utf8"), {
  "next/server": next, "../../../../lib/server": server,
});

test("OIDC start keeps binding material in an httpOnly Lax cookie", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ authorization_url: "https://idp.example/authorize?state=state", flow_token: "binding-secret", expires_in: 600 }));
  const res = await start.GET(new next.NextRequest("https://portal.example/api/auth/oidc/start"));
  assert.equal(res.status, 303);
  assert.match(res.headers.get("location"), /^https:\/\/idp.example\//);
  const binding = res.cookies.get("ailab_oidc_flow");
  assert.equal(binding.value, "binding-secret");
  assert.equal(binding.httpOnly, true);
  assert.equal(binding.secure, true);
  assert.equal(binding.sameSite, "lax");
  assert.equal(await res.text(), "");
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("OIDC start rejects cross-site requests without contacting upstream", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({}); });
  const res = await start.GET(new next.NextRequest("https://portal.example/api/auth/oidc/start", { headers: { "sec-fetch-site": "cross-site" } }));
  assert.equal(res.status, 403);
  assert.equal(calls, 0);
});

test("OIDC callback exchanges on the server, sets the existing platform cookie and redirects without tokens", async (t) => {
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.deepEqual(JSON.parse(options.body), { state: "state", code: "code", error: null, flow_token: "binding-secret" });
    assert.equal(options.cache, "no-store");
    return Response.json({ token: "platform-secret", expires_in: 3600 });
  });
  const req = new next.NextRequest("https://portal.example/api/auth/oidc/callback?state=state&code=code", { headers: { cookie: "ailab_oidc_flow=binding-secret" } });
  const res = await callback.GET(req);
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/");
  const cookie = res.cookies.get("ailab_token");
  assert.equal(cookie.value, "platform-secret");
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.sameSite, "strict");
  assert.equal(cookie.secure, true);
  assert.equal(res.cookies.get("ailab_oidc_flow").maxAge, 0);
  assert.equal(await res.text(), "");
});

test("missing binding and refused OIDC callback clear the flow and never set a platform token", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ detail: "refused" }, { status: 401 }); });
  for (const cookie of ["", "ailab_oidc_flow=binding-secret"]) {
    const res = await callback.GET(new next.NextRequest("https://portal.example/api/auth/oidc/callback?state=s&code=c", { headers: { cookie } }));
    assert.match(res.headers.get("location"), /\/login\?error=oidc$/);
    assert.equal(res.cookies.get("ailab_token"), undefined);
    assert.equal(res.cookies.get("ailab_oidc_flow").maxAge, 0);
  }
  assert.equal(calls, 1);
});

test("proxy preserves the array-list keyset cursor header", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json([], { headers: { "x-next-cursor": "cursor" } }));
  const res = await proxy.GET(new next.NextRequest("https://portal.example/api/v1/admin/departments", { headers: { cookie: "ailab_token=session" } }), { params: Promise.resolve({ path: ["admin", "departments"] }) });
  assert.equal(res.headers.get("x-next-cursor"), "cursor");
});

test("proxy preserves the reader snapshot security headers", async (t) => {
  const headers = { "content-security-policy": "sandbox allow-popups; script-src 'none'", "referrer-policy": "no-referrer" };
  t.mock.method(globalThis, "fetch", async () => new Response("<p>article</p>", { headers }));
  const res = await proxy.GET(new next.NextRequest("https://portal.example/api/v1/reader/render?url=https://example.com", { headers: { cookie: "ailab_token=session" } }), { params: Promise.resolve({ path: ["reader", "render"] }) });
  for (const [key, value] of Object.entries(headers)) assert.equal(res.headers.get(key), value);
});
