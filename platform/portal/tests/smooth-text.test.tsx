// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useSmoothText } from "../components/motion/smooth-text";

let frames: Map<number, FrameRequestCallback>;
let serial: number;
let now: number;
function Probe({ text, active = true }: { text: string; active?: boolean }) {
  return <div data-testid="text">{useSmoothText(text, active)}</div>;
}
function advance(ms: number) {
  now += ms;
  const pending = [...frames.values()];
  frames.clear();
  act(() => pending.forEach((f) => f(now)));
}
beforeEach(() => {
  frames = new Map(); serial = 0; now = 0;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => { frames.set(++serial, f); return serial; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("a burst drains within 80ms and leaves no idle animation loop", () => {
  const view = render(<Probe text="" />);
  expect(frames.size).toBe(0);
  view.rerender(<Probe text={"字".repeat(120)} />);
  advance(40);
  expect(screen.getByTestId("text").textContent?.length).toBe(60);
  advance(40);
  expect(screen.getByTestId("text").textContent?.length).toBe(120);
  expect(frames.size).toBe(0);
});

test("terminal text and replacement answers show immediately and cancel old frames", () => {
  const view = render(<Probe text="" />);
  view.rerender(<Probe text="旧回答" />);
  expect(frames.size).toBe(1);
  view.rerender(<Probe text="完整回答" active={false} />);
  expect(screen.getByTestId("text").textContent).toBe("完整回答");
  expect(frames.size).toBe(0);
  view.rerender(<Probe text="新" />);
  expect(screen.getByTestId("text").textContent).toBe("新");
});

test("UI source is released as a block instead of character by character", () => {
  const view = render(<Probe text="" />);
  const source = '```openui\nroot = Stack([Text("内容")])\n```';
  view.rerender(<Probe text={source} />);
  advance(40);
  expect(screen.getByTestId("text").textContent).toBe(source);
  expect(frames.size).toBe(0);
});
