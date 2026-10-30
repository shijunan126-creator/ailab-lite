// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, test, vi } from "vitest";
import { useFollow } from "../lib/use-follow";

// jsdom has no layout: a container whose content height the test sets, and frames the test advances
let frames: FrameRequestCallback[] = [];
let now = 0;
let resized: () => void = () => {};
beforeEach(() => {
  frames = [];
  now = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => (frames = []));
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("ResizeObserver", class { constructor(cb: () => void) { resized = cb; } observe() {} disconnect() {} });
});
afterEach(() => vi.unstubAllGlobals());

function box(height: number) {
  const el = document.createElement("div");
  let top = 0;
  Object.defineProperty(el, "clientHeight", { value: 400 });
  Object.defineProperty(el, "scrollHeight", { get: () => height, configurable: true });
  Object.defineProperty(el, "scrollTop", { get: () => top, set: (v: number) => (top = Math.max(0, Math.min(v, height - 400))) });
  return { el, grow: (by: number) => ((height += by), resized()) };
}
const tick = (ms = 16) => {
  now += ms;
  const run = frames;
  frames = [];
  run.forEach((cb) => cb(now));
};
const scrollEvent = (el: HTMLElement) => ({ currentTarget: el }) as unknown as React.UIEvent<HTMLElement>;

test("new content glides into view over frames instead of jumping", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600; // at the bottom
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  grow(120);
  assert.equal(el.scrollTop, 600); // not yet: the move happens in frames
  tick();
  assert.ok(el.scrollTop > 600 && el.scrollTop < 720, String(el.scrollTop));
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 720);
  assert.equal(frames.length, 0); // nothing keeps running at the bottom
});

test("scrolling up stops following; returning to the bottom resumes it", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  el.scrollTop = 300;
  result.current.onScroll(scrollEvent(el));
  grow(200);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 300);
  el.scrollTop = 800; // the reader scrolls back down
  result.current.onScroll(scrollEvent(el));
  grow(100);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 900);
});

test("its own glide is not mistaken for the reader scrolling away", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  grow(300);
  tick();
  result.current.onScroll(scrollEvent(el)); // the scroll event of the frame above
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 900);
});

test("a gap of more than a screen and a half is closed at once", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  grow(5000);
  assert.equal(el.scrollTop, 5600);
});
