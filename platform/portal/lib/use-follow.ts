"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

// Keeps a scroll container at its newest content while that content grows (the conversation, a run's work or
// thinking): it glides to the bottom instead of jumping, and only while the reader is there. Scrolling away from the
// bottom, by wheel, touch, keys or the scrollbar, stops following; coming back to the bottom resumes it. Driven by
// content size changes, so nothing runs per frame once the bottom is reached.
//
// A stop (a new answer's start) caps the following: the view moves with the answer until that start reaches the top and
// then stays, so a long answer is read from its beginning. Once the reader scrolls to the bottom past the stop, the stop
// is dropped and the view sticks to the bottom again.

const EASE_MS = 110; // time constant: about 90% of the gap closes in a quarter second
const JUMP_SCREENS = 1.5; // farther than this (opening a conversation, a large insert) is not animated

export type Follow = {
  /** Whether the container follows new content; set it to resume following (after a send) or stop (prepend). */
  following: React.RefObject<boolean>;
  /** Brings the bottom into view now when following: animated if near, instantly if far. */
  settle: (instant?: boolean) => void;
  /** The reader asked for the newest content: glide there (a far one lands a screen short first) and follow again. */
  toBottom: () => void;
  /** Where following stops: the scrollTop that puts the reader's starting point at the top, or null while it is not laid out. */
  stopAt: (at: (() => number | null) | null) => void;
  /** Where the view is or is heading: the follow target while following, else where the reader is. */
  heading: () => number;
  onScroll: (e: React.UIEvent<HTMLElement>) => void;
  /** Ref for the element whose growth is followed (it may be swapped for another; the new one is observed). */
  content: (node: HTMLElement | null) => void;
};

export function useFollow(scroller: React.RefObject<HTMLElement | null>, { slack = 24, enabled = true } = {}): Follow {
  const following = useRef(true);
  const active = useRef(enabled);
  active.current = enabled;
  const observer = useRef<ResizeObserver | null>(null);
  const frame = useRef(0);
  const placed = useRef<number | null>(null); // the last scrollTop this hook wrote, to tell its own scrolls from the reader's
  const stopRef = useRef<(() => number | null) | null>(null);

  // the scrollTop following heads for: the bottom, or the stop when that comes first
  const target = useCallback((el: HTMLElement) => {
    const bottom = el.scrollHeight - el.clientHeight;
    const at = stopRef.current?.();
    return at == null ? bottom : Math.max(0, Math.min(bottom, at));
  }, []);

  const stop = () => {
    cancelAnimationFrame(frame.current);
    frame.current = 0;
  };

  const settle = useCallback((instant = false) => {
    const el = scroller.current;
    if (!el || !following.current || !active.current) return;
    const gap = target(el) - el.scrollTop;
    if (gap <= 0.5) return; // following only ever moves down: a stop above the view is already behind the reader
    if (instant || gap > el.clientHeight * JUMP_SCREENS || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      stop();
      el.scrollTop = target(el);
      placed.current = el.scrollTop;
      return;
    }
    if (frame.current) return; // already gliding; it reads the moving target each frame
    let last: number | null = null; // frame times only: the first frame moves by one frame's worth
    const step = (now: number) => {
      const goal = target(el);
      const left = goal - el.scrollTop;
      if (!following.current || left <= 0) {
        frame.current = 0;
        return;
      }
      const moved = left * (1 - Math.exp(-(last === null ? 16 : now - last) / EASE_MS));
      last = now;
      // the easing never quite arrives: the last pixel is placed exactly, then nothing runs until the content grows
      const done = left - moved < 1;
      el.scrollTop = done ? goal : el.scrollTop + moved;
      placed.current = el.scrollTop;
      frame.current = done ? 0 : requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }, [scroller, target]);

  const toBottom = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    following.current = true;
    stopRef.current = null;
    const bottom = el.scrollHeight - el.clientHeight;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduced && bottom - el.scrollTop > el.clientHeight * JUMP_SCREENS) {
      stop();
      el.scrollTop = bottom - el.clientHeight;
      placed.current = el.scrollTop;
    }
    settle(reduced);
  }, [scroller, settle]);

  const onScroll = useCallback((e: React.UIEvent<HTMLElement>) => {
    const el = e.currentTarget;
    // a scroll this hook made reports the position it wrote (browsers round it)
    if (placed.current !== null && Math.abs(el.scrollTop - placed.current) < 2) return;
    placed.current = null;
    following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
    if (!following.current) stop();
    // at the bottom with the stop reached: from here on the newest content is followed. A short answer that has not
    // reached its stop keeps it, so scrolling up and back down does not lose the place it will stop at.
    else if (stopRef.current) {
      const at = stopRef.current();
      if (at == null || at <= el.scrollTop + 1) stopRef.current = null;
    }
  }, [slack]);

  const heading = useCallback(() => {
    const el = scroller.current;
    if (!el) return 0;
    return following.current && active.current ? Math.max(el.scrollTop, target(el)) : el.scrollTop;
  }, [scroller, target]);

  const stopAt = useCallback((at: (() => number | null) | null) => {
    stopRef.current = at;
  }, []);

  const content = useCallback((node: HTMLElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node) return;
    observer.current = new ResizeObserver(() => settle());
    observer.current.observe(node);
  }, [settle]);

  useEffect(() => () => {
    observer.current?.disconnect();
    stop();
  }, []);

  return useMemo(() => ({ following, settle, toBottom, stopAt, heading, onScroll, content }), [settle, toBottom, stopAt, heading, onScroll, content]);
}
