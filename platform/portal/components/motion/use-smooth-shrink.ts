"use client";

import { type RefObject, useLayoutEffect, useRef } from "react";
import { durationMs } from "./duration";

/** When the content of `box` (its single child) loses height (a thought folds, rows unmount), `box` glides from the height it
 * showed to its new one instead of snapping, so everything below it moves smoothly too. Growth is left alone: it comes from
 * streaming, a line at a time, or from the reader opening something.
 * The glide starts in the commit that shrank the content, so nothing (a paint, a scroll anchor, a measurement) sees the new
 * height first; a ResizeObserver catches content that shrinks without this component committing. */
export function useSmoothShrink(box: RefObject<HTMLElement | null>) {
  const state = useRef({ content: -1, shown: -1, animation: null as Animation | null });
  const check = useRef(() => {
    const el = box.current;
    const inner = el?.firstElementChild;
    if (!el || !inner || typeof el.animate !== "function") return;
    const s = state.current;
    // the box's own height is animated mid-glide; only a change of its content means there is something new to do
    const content = inner.getBoundingClientRect().height;
    if (content === s.content) return;
    s.content = content;
    // mid-glide the box shows the animated height; otherwise what it showed after the previous change
    const from = s.animation ? el.getBoundingClientRect().height : s.shown;
    s.animation?.cancel();
    s.animation = null;
    const to = el.getBoundingClientRect().height;
    s.shown = to;
    if (from < 0 || from - to < 1 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const root = getComputedStyle(document.documentElement);
    const duration = durationMs(root.getPropertyValue("--t-slow")) || 280;
    const easing = root.getPropertyValue("--ease-in-out").trim() || "ease-in-out";
    const glide = el.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration, easing });
    s.animation = glide;
    glide.onfinish = () => {
      if (s.animation === glide) s.animation = null;
    };
  }).current;

  useLayoutEffect(check);

  useLayoutEffect(() => {
    const inner = box.current?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(check);
    observer.observe(inner);
    return () => {
      observer.disconnect();
      state.current.animation?.cancel();
    };
  }, [box, check]);
}
