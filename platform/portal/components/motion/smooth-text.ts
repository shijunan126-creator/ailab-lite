"use client";

import { useEffect, useRef, useState } from "react";
import { insideUiFence } from "../../lib/answer-parts";

const DRAIN_MS = 80; // with the frame batching, every delta is on screen within the 150ms budget (HLD §10)
const JUMP = 4000;

/** Release a burst within 80ms, then stop scheduling frames until the next delta. */
export function useSmoothText(text: string, active: boolean): string {
  const [shown, setShown] = useState(text.length);
  const shownRef = useRef(shown);
  const previous = useRef(text);
  const reset = !text.startsWith(previous.current);
  const immediate = !active || reset ||
    (typeof window !== "undefined" && (document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches));

  useEffect(() => {
    previous.current = text;
    if (immediate || text.length - shownRef.current > JUMP) {
      shownRef.current = text.length;
      setShown(text.length);
      return;
    }
    const from = shownRef.current;
    if (from >= text.length) return;
    const started = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / DRAIN_MS);
      let length = from + Math.ceil((text.length - from) * progress);
      if (document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches) length = text.length;
      if (text.includes("```openui") && insideUiFence(text.slice(0, length))) {
        const close = text.indexOf("\n```", length);
        length = close < 0 ? text.length : close + 4;
      }
      shownRef.current = length;
      setShown(length);
      if (length < text.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [text, immediate]);

  return immediate || shown >= text.length ? text : text.slice(0, shown);
}
