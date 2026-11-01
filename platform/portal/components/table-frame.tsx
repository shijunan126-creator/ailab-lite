"use client";

// A table with its copy action: a small floating button in the corner, there while the table is pointed at or touched.
import { useRef, type ReactNode } from "react";
import { tableClip } from "../lib/clipboard";
import { useTouchReveal } from "../lib/use-touch-reveal";
import CopyButton from "./copy-button";

/** `markdown`: the source the table was written in, copied as the plain-text flavour; otherwise one is built. */
export default function TableFrame({ markdown, children }: { markdown?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [touched, reveal] = useTouchReveal(ref);
  return (
    <div ref={ref} className="table-frame" data-touched={touched || undefined} onPointerDown={reveal}>
      {children}
      <div className="float-tools table-tools" data-capture-skip="">
        <CopyButton
          label="复制表格"
          text={() => {
            const table = ref.current?.querySelector("table");
            return table ? tableClip(table, markdown) : { plain: markdown ?? "" };
          }}
        />
      </div>
    </div>
  );
}
