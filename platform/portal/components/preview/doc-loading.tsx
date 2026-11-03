"use client";

import { useEffect, useState, type CSSProperties } from "react";

/** The outline a waiting preview draws: what the document will look like, not a "loading" word. */
export type LoadingShape = "page" | "slide" | "sheet" | "text" | "media";

const SHAPES: [RegExp, LoadingShape][] = [
  [/\.(pptx?|odp|key)$/i, "slide"],
  [/\.(xlsx?|xlsm|ods|csv|tsv)$/i, "sheet"],
  [/\.(png|jpe?g|gif|webp|bmp|svg|avif|heic|tiff?)$/i, "media"],
  [/\.(docx?|pdf|odt|rtf|html?|xhtml)$/i, "page"],
];

export function loadingShape(filename: string): LoadingShape {
  return SHAPES.find(([pattern]) => pattern.test(filename))?.[1] ?? "text";
}

/** Shown only once a wait outlasts this, so quick loads never flash. */
const DELAY_MS = 180;
const PULSE_MS = 1600;
/** A placeholder mounted this soon after another one unmounted continues that wait. */
const HANDOFF_MS = 250;

// One wait often passes through several components (file record → preview job → renderer). The chain keeps
// it one visual wait: a successor inherits the start time, so it appears already shown and pulses in phase.
let chain = { start: 0, ended: Number.NEGATIVE_INFINITY };

function timing(): { start: number; style: CSSProperties } {
  const now = performance.now();
  const start = now - chain.ended < HANDOFF_MS ? chain.start : now;
  const elapsed = now - start;
  return {
    start,
    style: { "--doc-loading-delay": `${Math.max(0, DELAY_MS - elapsed)}ms`, "--doc-loading-phase": `${-(elapsed % PULSE_MS)}ms` } as CSSProperties,
  };
}

const LINES = ["title", "", "", "short", "gap", "", "", "", "mid", "gap", "", "short"];

export default function DocLoading({ shape }: { shape: LoadingShape }) {
  const [{ start, style }] = useState(timing);
  useEffect(() => () => void (chain = { start, ended: performance.now() }), [start]);
  return (
    <div className="doc-loading" data-shape={shape} role="status" aria-label="正在加载" style={style}>
      {shape === "sheet" ? (
        <div className="doc-loading-grid" />
      ) : shape === "media" ? (
        <div className="doc-loading-media" />
      ) : (
        (shape === "text" ? [0] : [0, 1]).map((n) => (
          <div key={n} className="doc-loading-paper">
            {n === 0 ? (shape === "slide" ? ["title", "", "mid"] : LINES).map((line, i) => <i key={i} className={line || undefined} />) : null}
          </div>
        ))
      )}
    </div>
  );
}
