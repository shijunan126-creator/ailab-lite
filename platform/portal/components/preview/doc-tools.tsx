"use client";

import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** The title bar a preview sits under (the side pane's, the full-screen viewer's). A preview puts its own document
 * controls there instead of drawing a second bar: one row names the file and holds everything that acts on it. */
export const DocToolsSlot = createContext<HTMLElement | null>(null);

export function DocTools({ children }: { children: ReactNode }) {
  const slot = useContext(DocToolsSlot);
  return slot ? createPortal(children, slot) : null;
}
