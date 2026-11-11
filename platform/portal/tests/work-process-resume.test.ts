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
