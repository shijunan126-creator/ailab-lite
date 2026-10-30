import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { boundBlock, saveBlock, startingState, type UIState } from "../lib/ui-state";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("a block keeps only what the API stores: scalars and OpenUI field wrappers, within the bounds", () => {
  const out = boundBlock({
    $seats: 60,
    $tier: "专业版",
    $yearly: true,
    $nothing: null,
    "Tabs:t1": { value: "饼图", componentType: "Tabs" },
    "legend:c1": { value: ["新增"], componentType: null },
    $nan: Number.NaN,
    $deep: { a: { b: 1 } },
    $list: [1, 2],
    $long: "x".repeat(1001),
    ["k".repeat(121)]: 1,
  });
  assert.deepEqual(out, {
    $seats: 60,
    $tier: "专业版",
    $yearly: true,
    $nothing: null,
    "Tabs:t1": { value: "饼图", componentType: "Tabs" },
    "legend:c1": { value: ["新增"], componentType: null },
  });
  const many = Object.fromEntries(Array.from({ length: 80 }, (_, i) => [`$v${i}`, i]));
  assert.equal(Object.keys(boundBlock(many)).length, 64);
});

test("a run starts from the server's state until this page has a newer one", () => {
  const saved: UIState = { v: 1, blocks: { "0": { $seats: 40 } } };
  assert.equal(startingState("run_a", saved), saved);
  assert.equal(startingState("run_a", { v: 2 } as unknown as UIState), null);
  assert.equal(startingState("run_a", null), null);
});

test("changes are saved once, shortly after the last one, merged with the other blocks", async () => {
  vi.useFakeTimers();
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => (calls.push({ url, body: JSON.parse(String(init.body)) }), new Response(null, { status: 204 })));
  const base: UIState = { v: 1, blocks: { "1": { $x: 1 } } };
  saveBlock("run_b", 0, { $seats: 25 }, base);
  saveBlock("run_b", 0, { $seats: 60 }, base);
  assert.equal(calls.length, 0);
  await vi.advanceTimersByTimeAsync(700);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/runs\/run_b\/ui-state$/);
  assert.deepEqual(calls[0].body, { v: 1, blocks: { "0": { $seats: 60 }, "1": { $x: 1 } } });
  // the page now prefers its own copy over a detail fetched before the save landed
  assert.deepEqual(startingState("run_b", base)?.blocks["0"], { $seats: 60 });
});

test("state over the stored size stays on this page instead of failing the save", async () => {
  vi.useFakeTimers();
  let calls = 0;
  vi.stubGlobal("fetch", async () => (calls++, new Response(null, { status: 204 })));
  const big = Object.fromEntries(Array.from({ length: 60 }, (_, i) => [`$t${i}`, "字".repeat(400)]));
  saveBlock("run_c", 0, big, null);
  await vi.advanceTimersByTimeAsync(700);
  assert.equal(calls, 0);
  assert.equal(Object.keys(startingState("run_c", null)?.blocks["0"] ?? {}).length, 60);
});
