// The node:test context (`t.mock`, `t.after`) these suites were written against, on Vitest.
import { afterEach, vi } from "vitest";

const cleanups = [];
afterEach(async () => {
  while (cleanups.length) await cleanups.pop()();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

export const t = {
  after: (fn) => cleanups.push(fn),
  mock: {
    method: (target, name, impl) => vi.spyOn(target, name).mockImplementation(impl),
    timers: { enable: ({ apis }) => vi.useFakeTimers({ toFake: apis }), tick: (ms) => vi.advanceTimersByTime(ms) },
  },
};
