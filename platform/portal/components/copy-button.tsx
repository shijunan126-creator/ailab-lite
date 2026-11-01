"use client";

import { useEffect, useRef, useState } from "react";
import { writeClipboard, type Clip } from "../lib/clipboard";
import { IconCheck, IconCopy } from "./icons";

/** Copies text, or HTML with its plain-text form; the icon turns into a check for a moment so the click needs no message. */
export default function CopyButton({ text, label = "复制", className = "" }: { text: string | (() => string | Clip); label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  async function copy() {
    const value = typeof text === "function" ? text() : text;
    try {
      await writeClipboard(typeof value === "string" ? { plain: value } : value);
    } catch {
      return;
    }
    setDone(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setDone(false), 1400);
  }
  return (
    <button type="button" className={`icon-btn act ${done ? "done" : ""} ${className}`} data-capture-skip="" onClick={copy} aria-label={done ? "已复制" : label} title={done ? "已复制" : label}>
      <span className="swap">{done ? <IconCheck size={15} /> : <IconCopy size={15} />}</span>
    </button>
  );
}
