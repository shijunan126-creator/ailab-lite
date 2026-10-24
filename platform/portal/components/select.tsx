"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconCheck, IconChevronDown } from "./icons";

export type SelectOption = { value: string; label: string; group?: string };

const GAP = 4;
const MAX_H = 300;

/** A dropdown drawn by the page, not the browser. Its list is rendered into <body> so a dialog's scroll box can't clip it. */
export default function Select({
  value,
  options,
  onChange,
  disabled,
  className = "",
  "aria-label": label,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}) {
  const [at, setAt] = useState<{ top: number; left: number; width: number; up: boolean } | null>(null);
  const [active, setActive] = useState(0);
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);
  const current = options.find((o) => o.value === value);

  function open() {
    const r = button.current!.getBoundingClientRect();
    const up = r.bottom + GAP + Math.min(MAX_H, options.length * 36 + 12) > window.innerHeight && r.top > window.innerHeight - r.bottom;
    setAt({ top: up ? r.top - GAP : r.bottom + GAP, left: r.left, width: r.width, up });
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
  }
  const close = (focus = true) => {
    setAt(null);
    if (focus) button.current?.focus();
  };
  function pick(o: SelectOption) {
    close();
    if (o.value !== value) onChange(o.value);
  }

  useEffect(() => {
    if (!at) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !button.current?.contains(t)) close(false);
    };
    const onMove = (e: Event) => !list.current?.contains(e.target as Node) && close(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [at]);

  useLayoutEffect(() => {
    if (at) list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [at, active]);

  function onKey(e: React.KeyboardEvent) {
    if (disabled) return;
    if (!at) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        open();
      }
      return;
    }
    if (e.key === "Escape" || e.key === "Tab") {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation(); // closes the list, not the dialog it sits in
      }
      close(e.key === "Escape");
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[active]) pick(options[active]);
    }
  }

  return (
    <>
      <button
        ref={button}
        type="button"
        className={`select ${className}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={!!at}
        aria-label={label}
        onClick={() => (at ? close() : open())}
        onKeyDown={onKey}
      >
        <span className="select-value">{current?.label ?? ""}</span>
        <IconChevronDown size={14} />
      </button>
      {at
        ? createPortal(
            <div
              ref={list}
              className={`select-list ${at.up ? "up" : ""}`}
              role="listbox"
              aria-label={label}
              style={{ left: at.left, minWidth: at.width, maxHeight: MAX_H, ...(at.up ? { bottom: window.innerHeight - at.top } : { top: at.top }) }}
            >
              {options.map((o, i) => (
                <div key={o.value} className="select-row">
                  {o.group && o.group !== options[i - 1]?.group ? <div className="select-group">{o.group}</div> : null}
                  <button
                    type="button"
                    role="option"
                    data-index={i}
                    aria-selected={o.value === value}
                    className={`select-option ${i === active ? "active" : ""}`}
                    onMouseMove={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(o)}
                    onKeyDown={onKey}
                  >
                    <span className="grow">{o.label}</span>
                    {o.value === value ? <IconCheck size={14} /> : null}
                  </button>
                </div>
              ))}
            </div>,
            document.body
          )
        : null}
    </>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "HH:MM" as an hour and a minute list; a minute off the five-minute grid (set elsewhere) stays selectable. */
export function TimeSelect({ value, onChange, "aria-label": label }: { value: string; onChange: (value: string) => void; "aria-label"?: string }) {
  const [h, m] = (/^\d{2}:\d{2}$/.test(value) ? value : "09:00").split(":");
  const minutes = Array.from({ length: 12 }, (_, i) => pad(i * 5));
  if (!minutes.includes(m)) minutes.push(m), minutes.sort();
  return (
    <span className="time-select" role="group" aria-label={label}>
      <Select aria-label="时" value={h} onChange={(v) => onChange(`${v}:${m}`)} options={Array.from({ length: 24 }, (_, i) => ({ value: pad(i), label: pad(i) }))} />
      <span aria-hidden="true">:</span>
      <Select aria-label="分" value={m} onChange={(v) => onChange(`${h}:${v}`)} options={minutes.map((x) => ({ value: x, label: x }))} />
    </span>
  );
}
