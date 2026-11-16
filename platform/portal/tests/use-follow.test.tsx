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
  document.body.append(el);
  return { el, grow: (by: number) => ((height += by), resized()) };
}
const tick = (ms = 16) => {
  now += ms;
  const run = frames;
  frames = [];
  run.forEach((cb) => cb(now));
};
const scrollEvent = (el: HTMLElement) => ({ currentTarget: el }) as unknown as React.UIEvent<HTMLElement>;
/** The reader scrolls with the wheel to `top`. */
function wheel(el: HTMLElement, top: number, follow: { onScroll: (e: React.UIEvent<HTMLElement>) => void }) {
  el.dispatchEvent(new Event("wheel", { bubbles: true }));
  el.scrollTop = top;
  follow.onScroll(scrollEvent(el));
}

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
  wheel(el, 300, result.current);
  grow(200);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 300);
  wheel(el, 800, result.current); // the reader scrolls back down
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

test("the browser moving the view, not the reader, keeps following", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  // a chart above the view renders: scroll anchoring moves the view down by 200, the 100 that grew below is out of view
  grow(300);
  el.scrollTop = 800;
  result.current.onScroll(scrollEvent(el));
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 900);
  assert.equal(result.current.following.current, true);
});

test("a scrollbar drag away from the bottom stops following until it ends at the bottom", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  el.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  el.scrollTop = 200;
  result.current.onScroll(scrollEvent(el));
  window.dispatchEvent(new Event("pointerup"));
  grow(200);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 200);
});

test("a gap of more than a screen and a half is closed at once", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  grow(5000);
  assert.equal(el.scrollTop, 5600);
});

test("a new answer is followed until its start reaches the top, then the view stays", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  result.current.stopAt(() => 800); // the answer's start sits at the top when scrollTop is 800
  grow(100); // still short: the bottom comes first
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 700);
  grow(600);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 800);
  grow(2000); // far below, but the answer is read from its start
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 800);
  assert.equal(result.current.following.current, true);
});

test("scrolling to the bottom past the stop sticks to the bottom again", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  result.current.stopAt(() => 700);
  grow(1000);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 700);
  wheel(el, 1000, result.current); // somewhere in the answer: nothing pulls the reader
  grow(100);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 1000);
  wheel(el, 1700, result.current); // the reader reaches the bottom
  grow(100);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 1800);
});

test("a short answer keeps its stop when the reader scrolls up and back down", () => {
  const { el, grow } = box(1000);
  el.scrollTop = 600;
  const { result } = renderHook(() => useFollow({ current: el }));
  act(() => result.current.content(document.createElement("div")));
  result.current.stopAt(() => 800);
  wheel(el, 300, result.current);
  wheel(el, 600, result.current);
  grow(1000);
  for (let i = 0; i < 40; i++) tick();
  assert.equal(el.scrollTop, 800);
});
