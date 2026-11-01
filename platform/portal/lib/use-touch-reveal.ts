"use client";

import { useEffect, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

/** Touch screens have no hover: a touch on the element reveals its actions until a touch lands outside it.
 * Returns whether they are revealed and the handler for the element's pointerdown (true when a touch revealed them). */
export function useTouchReveal(ref: RefObject<HTMLElement | null>): [boolean, (e: ReactPointerEvent) => boolean] {
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched) return;
    const away = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setTouched(false);
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [touched, ref]);
  return [
    touched,
    (e) => {
      if (e.pointerType === "mouse") return false;
      setTouched(true);
      return true;
    },
  ];
}
