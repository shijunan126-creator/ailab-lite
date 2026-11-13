"use client";

import { useEffect, useRef, useState } from "react";
import { durationMs } from "./duration";

/** For a `.collapse` region: whether its body should be mounted. A closed body stays mounted through the closing height
 * transition, so it shrinks instead of vanishing in one frame, and is released afterwards so folded Markdown costs nothing. */
export function useCollapse(open: boolean) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    if (!mounted) return;
    const node = ref.current;
    const ms = node ? Math.max(0, ...getComputedStyle(node).transitionDuration.split(",").map(durationMs)) : 0;
    if (!node || !ms) {
      setMounted(false);
      return;
    }
    const done = (event: TransitionEvent) => {
      if (event.target === node && event.propertyName === "grid-template-rows") setMounted(false);
    };
    node.addEventListener("transitionend", done);
    // transitionend does not fire when the transition is interrupted or the tab is hidden
    const timer = window.setTimeout(() => setMounted(false), ms + 100);
    return () => {
      node.removeEventListener("transitionend", done);
      window.clearTimeout(timer);
    };
  }, [open, mounted]);
  return { ref, body: open || mounted };
}
