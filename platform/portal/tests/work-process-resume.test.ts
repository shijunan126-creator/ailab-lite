import { expect, test } from "vitest";
import { reduceEvent } from "../lib/work-process";

// The crash announced the call, the restart only reports the result.
const events = [
  { type: "agent.tool_call", payload: { step: 2, name: "bash", arguments: { command: "make" } } },
  { type: "agent.tool_result", payload: { step: 2, name: "bash", ok: true, running: true, stdout: "" } },
];

test("a resumed command closes the step the crash opened instead of opening another", () => {
  let items: any[] = [];
  for (const event of events) items = reduceEvent(items, event as any);
  const steps = items.flatMap((item) => (item.kind === "steps" ? item.steps : []));
  expect(steps.filter((step: any) => step.name === "bash")).toHaveLength(1);
  expect(steps[0].status).not.toBe("running");
});

test("announcing the resumed call would leave the crash's step running", () => {
  // Why the Agent must not announce: the second tool_call opens a step the result never closes.
  const again = { type: "agent.tool_call", payload: { step: 2, name: "bash", arguments: { handle: "b".repeat(32) } } };
  let items: any[] = [];
  for (const event of [events[0], again, events[1]]) items = reduceEvent(items, event as any);
  const steps = items.flatMap((item) => (item.kind === "steps" ? item.steps : [])).filter((step: any) => step.name === "bash");
  expect(steps).toHaveLength(2);
  expect(steps[0].status).toBe("running");
});

test("results that come back out of order land on their own call", () => {
  // Three reads in one reply: announced back to back, answered as they finish.
  const calls = [
    { type: "agent.tool_call", payload: { step: 3, name: "read", call_id: "c0", arguments: { path: "/workspace/work/a.txt" } } },
    { type: "agent.tool_call", payload: { step: 3, name: "read", call_id: "c1", arguments: { path: "/workspace/work/b.txt" } } },
    { type: "agent.tool_call", payload: { step: 3, name: "read", call_id: "c2", arguments: { path: "/workspace/work/c.txt" } } },
  ];
  const results = [
    { type: "agent.tool_result", payload: { step: 3, name: "read", call_id: "c2", ok: true, stdout: "C" } },
    { type: "agent.tool_result", payload: { step: 3, name: "read", call_id: "c0", ok: false, error: "ENOENT" } },
    { type: "agent.tool_result", payload: { step: 3, name: "read", call_id: "c1", ok: true, stdout: "B" } },
  ];
  let items: any[] = [];
  for (const event of [...calls, ...results]) items = reduceEvent(items, event as any);
  const rows = items.flatMap((item) => (item.kind === "steps" ? item.steps : []));
  expect(rows.map((step: any) => [step.detail, step.status, step.output])).toEqual([
    ["/workspace/work/a.txt", "failed", "ENOENT"],
    ["/workspace/work/b.txt", "ok", "B"],
    ["/workspace/work/c.txt", "ok", "C"],
  ]);
});

test("an event without a call id still closes the last matching step", () => {
  // Recorded before call ids existed: name + step is the only pairing available.
  const events = [
    { type: "agent.tool_call", payload: { step: 1, name: "read", arguments: { path: "/workspace/work/a.txt" } } },
    { type: "agent.tool_result", payload: { step: 1, name: "read", ok: true, stdout: "A" } },
  ];
  let items: any[] = [];
  for (const event of events) items = reduceEvent(items, event as any);
  const rows = items.flatMap((item) => (item.kind === "steps" ? item.steps : []));
  expect(rows.map((step: any) => step.status)).toEqual(["ok"]);
});
