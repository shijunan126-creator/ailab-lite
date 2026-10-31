"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { placeMenu } from "../lib/menu-place";
import { IconCheck, IconMore } from "./icons";

export type RowAction =
  | { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; checked?: boolean; disabled?: boolean }
  | { heading: string }
  | "sep";

const ITEM_H = 36;

/** "⋯" at the end of a list row on the management pages: the row's secondary actions, so the row itself stays quiet. */
export default function RowMenu({ label, actions, disabled }: { label: string; actions: RowAction[]; disabled?: boolean }) {
  const [at, setAt] = useState<React.CSSProperties | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const items = actions.filter((a, i) => a !== "sep" || (i > 0 && i < actions.length - 1));
  const height = items.reduce((h, a) => h + (a === "sep" ? 9 : "heading" in a ? 28 : ITEM_H), 8);

  const open = !!at;
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && (menu.current?.contains(e.target as Node) || button.current?.contains(e.target as Node))) return;
      if (e.type === "keydown") {
        if ((e as KeyboardEvent).key !== "Escape") return;
        button.current?.focus();
      }
      setAt(null);
    };
    // A scroll the user did not mean as "dismiss" (momentum after the tap, the page shrinking under a filter) must not
    // swallow the menu: it follows its button and closes only once the button has left the screen.
    const follow = (e: Event) => {
      if (menu.current?.contains(e.target as Node)) return;
      const rect = button.current?.getBoundingClientRect();
      if (!rect || !button.current?.isConnected || rect.bottom < 0 || rect.top > window.innerHeight) setAt(null);
      else setAt(placeMenu(rect, 184, height));
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
    };
  }, [open, height]);

  return (
    <>
      <button
        ref={button}
        className={`icon-btn row-more ${at ? "open" : ""}`}
        aria-label={`更多操作 ${label}`}
        aria-haspopup="menu"
        aria-expanded={!!at}
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation();
          setAt(at ? null : placeMenu(button.current!.getBoundingClientRect(), 184, height));
        }}
      >
        <IconMore size={16} />
      </button>
      {at
        ? createPortal(
            <div ref={menu} className="pop-menu fixed" role="menu" aria-label={label} style={at}>
              {items.map((a, i) =>
                a === "sep" ? (
                  <div key={i} className="menu-sep" />
                ) : "heading" in a ? (
                  <div key={i} className="menu-label" role="presentation">{a.heading}</div>
                ) : (
                  <button
                    key={i}
                    className={`menu-item ${a.danger ? "danger" : ""} ${a.checked ? "on" : ""}`}
                    role={a.checked === undefined ? "menuitem" : "menuitemradio"}
                    aria-checked={a.checked}
                    disabled={a.disabled}
                    onClick={() => {
                      setAt(null);
                      a.onSelect();
                    }}
                  >
                    {a.icon}
                    <span className="grow">{a.label}</span>
                    {a.checked ? <span className="menu-check"><IconCheck size={14} /></span> : null}
                  </button>
                )
              )}
            </div>,
            document.body
          )
        : null}
    </>
  );
}
