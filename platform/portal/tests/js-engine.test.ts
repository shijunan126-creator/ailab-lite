import { describe, expect, it } from "vitest";
import { dropEngine, loadEngine, run } from "../lib/js/engine";
import { DEFAULT_LIMITS, type JsJob } from "../lib/js/protocol";

const job = (code: string, extra: Partial<JsJob> = {}): JsJob => ({ id: "t", code, timeout_ms: 1000, limits: DEFAULT_LIMITS, ...extra });

describe("browser JavaScript engine", () => {
  it("returns the program's last value as JSON, with input and logs", async () => {
    const r = run(await loadEngine(), job("console.log('rows', input.length); input.filter((x) => x.v > 1).map((x) => x.k)", { input: [{ k: "a", v: 1 }, { k: "b", v: 2 }] }));
    expect(r).toMatchObject({ ok: true, value: '["b"]', logs: "rows 2" });
    expect(r.stats.ms).toBeGreaterThanOrEqual(0);
  });

  it("waits for an async program", async () => {
    expect(run(await loadEngine(), job("(async () => { await null; return 6 * 7 })()"))).toMatchObject({ ok: true, value: "42" });
    expect(run(await loadEngine(), job("Promise.reject(new TypeError('no'))"))).toMatchObject({ ok: false, error: { type: "runtime", message: "TypeError: no" } });
  });

  it("stops an endless loop and a catastrophic regex at the deadline", async () => {
    const started = Date.now();
    expect(run(await loadEngine(), job("while (true) {}", { timeout_ms: 200 }))).toMatchObject({ ok: false, error: { type: "timeout" } });
    expect(run(await loadEngine(), job("/^(a+)+$/.test('a'.repeat(40) + 'b')", { timeout_ms: 200 }))).toMatchObject({ ok: false, error: { type: "timeout" } });
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it("stops at the memory limit and the output limit", async () => {
    const small = { ...DEFAULT_LIMITS, memory_mb: 8 };
    const started = Date.now();
    expect(run(await loadEngine(8), job("const a = []; while (true) a.push('x'.repeat(1 << 16))", { limits: small }))).toMatchObject({ ok: false, error: { type: "memory" } });
    dropEngine();
    expect(run(await loadEngine(8), job("const a = []; for (;;) a.push(new Array(1e5).fill(1))", { limits: small }))).toMatchObject({ ok: false, error: { type: "memory" } });
    dropEngine();
    expect(Date.now() - started).toBeLessThan(1500);
    expect(run(await loadEngine(), job("'x'.repeat(70 * 1024)"))).toMatchObject({ ok: false, error: { type: "output_limit" } });
  });

  it("reports syntax errors and has no host objects", async () => {
    expect(run(await loadEngine(), job("let = ;"))).toMatchObject({ ok: false, error: { type: "syntax" } });
    expect(run(await loadEngine(), job("[typeof fetch, typeof window, typeof require, typeof XMLHttpRequest]"))).toMatchObject({ ok: true, value: '["undefined","undefined","undefined","undefined"]' });
  });

  it("gives undefined as no value", async () => {
    expect(run(await loadEngine(), job("let x = 1;"))).toMatchObject({ ok: true, value: undefined });
  });
});
