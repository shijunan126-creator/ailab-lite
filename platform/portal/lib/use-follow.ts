"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

// Keeps a scroll container at its newest content while that content grows (the conversation, a run's work or
// thinking): it glides to the bottom instead of jumping, and only while the reader is there. Scrolling away from the
// bottom, by wheel, touch, keys or the scrollbar, stops following; coming back to the bottom resumes it. Driven by
// content size changes, so nothing runs per frame once the bottom is reached.

const EASE_MS = 110; // time constant: about 90% of the gap closes in a quarter second
const JUMP_SCREENS = 1.5; // farther than this (opening a conversation, a large insert) is not animated

export type Follow = {
  /** Whether the container follows new content; set it to resume following (after a send) or stop (prepend). */
  following: React.RefObject<boolean>;
  /** Brings the bottom into view now when following: animated if near, instantly if far. */
  settle: (instant?: boolean) => void;
  /** The reader asked for the newest content: glide there (a far one lands a screen short first) and follow again. */
  toBottom: () => void;
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

  const stop = () => {
    cancelAnimationFrame(frame.current);
    frame.current = 0;
  };

  const settle = useCallback((instant = false) => {
    const el = scroller.current;
    if (!el || !following.current || !active.current) return;
    const bottom = () => el.scrollHeight - el.clientHeight;
    const gap = bottom() - el.scrollTop;
    if (gap <= 0.5) return;
    if (instant || gap > el.clientHeight * JUMP_SCREENS || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      stop();
      el.scrollTop = bottom();
      placed.current = el.scrollTop;
      return;
    }
    if (frame.current) return; // already gliding; it reads the moving target each frame
    let last: number | null = null; // frame times only: the first frame moves by one frame's worth
    const step = (now: number) => {
      const target = bottom();
      const left = target - el.scrollTop;
      if (!following.current || left <= 0) {
        frame.current = 0;
        return;
      }
      const moved = left * (1 - Math.exp(-(last === null ? 16 : now - last) / EASE_MS));
      last = now;
      // the easing never quite arrives: the last pixel is placed exactly, then nothing runs until the content grows
      const done = left - moved < 1;
      el.scrollTop = done ? target : el.scrollTop + moved;
      placed.current = el.scrollTop;
      frame.current = done ? 0 : requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }, [scroller]);

  const toBottom = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    following.current = true;
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
  }, [slack]);

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

  return useMemo(() => ({ following, settle, toBottom, onScroll, content }), [settle, toBottom, onScroll, content]);
}
