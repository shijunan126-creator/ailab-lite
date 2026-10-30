"use client";

// What every chart shares: measured width, nice scales, legend, the pointer/keyboard readout and the screen-reader
// table. SVG in the host's tokens, so charts print as vectors and follow the theme without a chart library.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useIsStreaming, useStateField } from "@openuidev/react-lang";
import { formatNumber, withUnit } from "./data";

export const FONT = 12;
export const color = (i: number) => `var(--series-${(i % 6) + 1})`;
const useIso = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** The element's content width; 560 until measured (and in print, where it is laid out once). */
export function useWidth<T extends HTMLElement>(min = 200) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(560);
  useIso(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(Math.max(min, Math.round(el.clientWidth)));
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(min, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** A domain on round numbers covering [lo, hi], with about `count` ticks. */
export function niceScale(lo: number, hi: number, count = 4, whole = false) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, ticks: [0, 1] };
  if (lo === hi) {
    const pad = Math.abs(lo) || 1;
    lo -= pad / 2;
    hi += pad / 2;
  }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  // counts step by whole numbers: 2.5 people is not a tick
  const step = Math.max(whole ? 1 : 0, ([1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw * 0.999 && (!whole || Number.isInteger(s))) ?? raw));
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  return { min, max, ticks };
}

const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
export const tick = (v: number) => (Math.abs(v) >= 10000 ? compact.format(v) : formatNumber(Math.round(v * 100) / 100));
/** Rough rendered width of a label at 12px: CJK glyphs are square, Latin about half. */
export const textWidth = (s: string, size = FONT) => [...s].reduce((w, ch) => w + (/[⺀-￿]/.test(ch) ? size : size * 0.6), 0);
export const fit = (s: string, room: number, size = FONT) => {
  if (textWidth(s, size) <= room) return s;
  let out = "";
  for (const ch of s) {
    if (textWidth(out + ch + "…", size) > room) break;
    out += ch;
  }
  return out + "…";
};
export const value = (v: number, unit?: unknown, decimals?: unknown) => withUnit(formatNumber(v, decimals), unit);

/** Data that evaluated to the wrong shape. While the block is written a value can be missing for a moment, so
 * nothing shows until it is done. */
export function Malformed() {
  return useIsStreaming() ? null : <p className="gu-note">数据格式有误，无法显示</p>;
}

export type LegendItem = { name: string; color: string };
export function Legend({ items, hidden, onToggle }: { items: LegendItem[]; hidden?: Set<number>; onToggle?: (i: number) => void }) {
  if (items.length < 2) return null;
  return (
    <div className="gu-legend">
      {items.map((s, i) =>
        onToggle ? (
          <button key={i} type="button" aria-pressed={!hidden?.has(i)} onClick={() => onToggle(i)} title={hidden?.has(i) ? "显示" : "隐藏"}>
            <span className="gu-swatch" style={{ background: s.color }} />
            <span className="truncate">{s.name}</span>
          </button>
        ) : (
          <span key={i} className="gu-legend-item"><span className="gu-swatch" style={{ background: s.color }} /><span className="truncate">{s.name}</span></span>
        ),
      )}
    </div>
  );
}

/** The chart's numbers for screen readers. */
export function SrTable({ title, head, rows }: { title: string; head: string[]; rows: React.ReactNode[][] }) {
  return (
    <table className="sr-only">
      <caption>{title}</caption>
      <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => (j ? <td key={j}>{c}</td> : <th key={j}>{c}</th>))}</tr>)}</tbody>
    </table>
  );
}

export type ReadoutRow = { name: string; value: string; color?: string };
export function Readout({ x, y, flip, title, rows }: { x: number; y: number; flip: boolean; title: string; rows: ReadoutRow[] }) {
  return (
    <div className="gu-tip" style={{ left: x, top: y, translate: flip ? "calc(-100% - 12px) 0" : "12px 0" }} role="status">
      {title ? <div className="gu-tip-title">{title}</div> : null}
      {rows.map((r, i) => (
        <div key={i} className="gu-tip-row">
          {r.color ? <span className="gu-swatch" style={{ background: r.color }} /> : null}
          <span className="truncate">{r.name}</span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

/** Hovered or keyboard-chosen item, shared by the charts that read out one item at a time. */
export function useCursor(count: number) {
  const [at, setAt] = useState<number | null>(null);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!count) return;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") setAt((v) => (v == null ? 0 : Math.min(count - 1, v + 1)));
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") setAt((v) => (v == null ? count - 1 : Math.max(0, v - 1)));
    else if (e.key === "Escape") setAt(null);
    else return;
    e.preventDefault();
  };
  return { at, setAt, onKeyDown, onBlur: () => setAt(null) };
}

/** Series the reader switched off. In the answer's state, so it is saved with the other controls. */
export function useHidden(key: string) {
  const field = useStateField<number[]>(key.slice(0, 120));
  const hidden = new Set(Array.isArray(field.value) ? field.value.filter((i) => Number.isInteger(i)) : []);
  const toggle = (i: number) => {
    const next = new Set(hidden);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    field.setValue([...next].sort((a, b) => a - b));
  };
  return { hidden, toggle };
}

export function Frame({ title, unit, children }: { title: string; unit?: unknown; children: React.ReactNode }) {
  return (
    <figure className="gu-chart">
      {title || unit ? (
        <figcaption>
          {title}
          {typeof unit === "string" && unit ? <span className="gu-chart-unit">{unit}</span> : null}
        </figcaption>
      ) : null}
      {children}
    </figure>
  );
}

type Point = [number, number];
const linear = (pts: Point[]) => pts.map(([x, y], i) => `${i ? "L" : "M"}${x},${y}`).join("");
const stepAfter = (pts: Point[]) => pts.map(([x, y], i) => (i ? `H${x}V${y}` : `M${x},${y}`)).join("");
/** Monotone cubic through the points (Fritsch–Carlson): smooth without overshooting a peak the data does not have. */
function monotone(pts: Point[]) {
  const n = pts.length;
  if (n < 3) return linear(pts);
  const dx = pts.slice(1).map((p, i) => p[0] - pts[i][0]);
  const m = pts.slice(1).map((p, i) => (p[1] - pts[i][1]) / (dx[i] || 1));
  const t = pts.map((_, i) => (i === 0 ? m[0] : i === n - 1 ? m[n - 2] : m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2));
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) {
      t[i] = t[i + 1] = 0;
      continue;
    }
    const a = t[i] / m[i];
    const b = t[i + 1] / m[i];
    const s = a * a + b * b;
    if (s > 9) {
      const k = 3 / Math.sqrt(s);
      t[i] = k * a * m[i];
      t[i + 1] = k * b * m[i];
    }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${pts[i][0] + h},${pts[i][1] + t[i] * h},${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h},${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}
export type Curve = "linear" | "smooth" | "step";
/** A path through the points; a gap (NaN) splits it. */
export function path(pts: Point[], curve: Curve = "linear") {
  const draw = curve === "smooth" ? monotone : curve === "step" ? stepAfter : linear;
  const runs: Point[][] = [[]];
  for (const p of pts) {
    if (Number.isFinite(p[1])) runs[runs.length - 1].push(p);
    else if (runs[runs.length - 1].length) runs.push([]);
  }
  return runs.filter((r) => r.length).map(draw).join("");
}
/** The area between a top line and a base line (both left to right), for stacked and filled charts. */
export function band(top: Point[], base: Point[], curve: Curve = "linear") {
  if (!top.length) return "";
  // a step is drawn as its corner points, so the base can be walked backwards with the same shape
  const corners = (pts: Point[]): Point[] => (curve === "step" ? pts.flatMap((p, i) => (i ? [[p[0], pts[i - 1][1]] as Point, p] : [p])) : pts);
  const shape = curve === "step" ? "linear" : curve;
  return `${path(corners(top), shape)}${path(corners(base).reverse(), shape).replace(/^M/, "L")}Z`;
}
