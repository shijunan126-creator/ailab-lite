import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { createBatcher } from "../lib/stream/batcher";

afterEach(() => vi.unstubAllGlobals());

function frames() {
  const pending: (() => void)[] = [];
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => pending.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => pending.splice(0));
  return () => pending.splice(0).forEach((cb) => cb());
}

test("events between two frames reach the store as one batch", () => {
  const paint = frames();
  const batches: number[][] = [];
  const b = createBatcher<number>((batch) => batches.push(batch));
  for (let i = 0; i < 50; i++) b.push(i);
  assert.equal(batches.length, 0);
  paint();
  assert.equal(batches.length, 1);
  assert.equal(batches[0].length, 50);
  b.push(50);
  paint();
  assert.deepEqual(batches[1], [50]);
});

test("flush delivers at once and cancel drops what waits", () => {
  frames();
  const batches: number[][] = [];
  const b = createBatcher<number>((batch) => batches.push(batch));
  b.push(1);
  b.flush();
  assert.deepEqual(batches, [[1]]);
  b.push(2);
  b.cancel();
  b.flush();
  assert.equal(batches.length, 1);
});
