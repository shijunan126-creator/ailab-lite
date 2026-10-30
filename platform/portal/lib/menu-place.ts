import type { CSSProperties } from "react";

/** Where a fixed, portalled menu opens from its trigger: on the preferred side when it fits, flush with the trigger's
 * start or end edge. Opening upward it is anchored by `bottom`, so its real height never leaves a gap, and it grows out
 * of the corner nearest the trigger. */
export function placeMenu(trigger: DOMRect, width: number, height: number, { align = "end", prefer = "below" }: { align?: "start" | "end"; prefer?: "below" | "above" } = {}): CSSProperties {
  const vh = window.innerHeight;
  const left = Math.max(8, Math.min(window.innerWidth - width - 8, align === "end" ? trigger.right - width : trigger.left));
  const fitsBelow = trigger.bottom + 4 + height <= vh - 8;
  const fitsAbove = trigger.top - 4 - height >= 8;
  const below = prefer === "below" ? fitsBelow || !fitsAbove : !fitsAbove && fitsBelow;
  const x = align === "end" ? "right" : "left";
  return below
    ? { top: trigger.bottom + 4, left, width, transformOrigin: `top ${x}` }
    : { bottom: vh - trigger.top + 4, left, width, transformOrigin: `bottom ${x}` };
}
