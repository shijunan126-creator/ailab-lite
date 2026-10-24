import { useEffect, useRef, useState } from "react";

/** An anchored menu: open/closed, closed again by a click outside its box or by Escape. */
export function usePopover<T extends HTMLElement = HTMLDivElement>() {
  const [open, setOpen] = useState(false);
  const box = useRef<T | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Element;
      // a dialog opened from the menu (confirm, library picker) is outside its box but still part of it
      if (box.current?.contains(target) || target.closest?.(".modal-scrim")) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !document.querySelector(".modal-scrim") && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return { open, setOpen, box };
}
