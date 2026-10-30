"use client";

// The core charts: line, area, bar, combo and pie. Drawn at the width they are given (measured), so axis text stays
// 12px instead of scaling with the column. Pointer and keyboard both read values out; the legend hides and shows
// series; every chart also carries its numbers as a table for screen readers (HLD §4.6.4).
import { useId, useState } from "react";
import { formatNumber, formatShare, goodness, type SeriesData } from "./data";
import { MAX_CHART_POINTS } from "./limits";
import { Frame, Legend, Readout, SrTable, band, color, fit, niceScale, path, textWidth, tick, useCursor, useHidden, useWidth, value, type Curve } from "./kit";

const H = 220;

type Layer = { kind: "bar" | "line" | "area"; series: SeriesData[]; right?: boolean };
export type CartesianProps = {
  sid?: string;
  title: string;
  categories: string[];
  layers: Layer[];
  unit?: string;
  rightUnit?: string;
  decimals?: number;
  stacked?: boolean;
  percent?: boolean;
  curve?: Curve;
  zero?: boolean;
  marks?: string[];
  reference?: number;
  referenceLabel?: string;
  better?: string;
};

function clip(categories: string[], layers: Layer[]) {
  const count = Math.max(1, layers.reduce((n, l) => n + l.series.length, 0));
  const per = Math.max(1, Math.floor(MAX_CHART_POINTS / count));
  return { categories: categories.slice(0, per), layers: layers.map((l) => ({ ...l, series: l.series.map((s) => ({ ...s, values: s.values.slice(0, per) })) })) };
}

/** Stacked series: each value's [base, top], positives upward and negatives downward from zero. */
function stack(series: SeriesData[], n: number, percent: boolean) {
  const totals = Array.from({ length: n }, (_, c) => series.reduce((a, s) => a + Math.abs(Number.isFinite(s.values[c]) ? s.values[c] : 0), 0));
  const up = new Array(n).fill(0);
  const down = new Array(n).fill(0);
  return series.map((s) =>
    Array.from({ length: n }, (_, c) => {
      let v = Number.isFinite(s.values[c]) ? s.values[c] : 0;
      if (percent) v = totals[c] ? (v / totals[c]) * 100 : 0;
      const at = v >= 0 ? up : down;
      const base = at[c];
      at[c] += v;
      return [base, at[c]] as [number, number];
    }),
  );
}

/** Line, area, vertical bar and combo charts: categories along x, one or two value axes. */
export function CartesianChart(props: CartesianProps) {
  const { sid, title, unit, rightUnit, decimals, stacked, percent, curve = "linear", marks = [], reference, referenceLabel, better } = props;
  const { categories, layers } = clip(props.categories, props.layers);
  const all = layers.flatMap((l, li) => l.series.map((s) => ({ ...s, kind: l.kind, right: !!l.right && !!rightUnit, layer: li })));
  const indexed = all.map((s, i) => ({ ...s, i }));
  const { hidden, toggle } = useHidden(`legend:${sid ?? title}`);
  const visible = indexed.filter((s) => !hidden.has(s.i));
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(categories.length);
  const n = Math.max(1, categories.length);
  const hasBars = visible.some((s) => s.kind === "bar");
  const stacking = stacked || percent;
  // stacked [base, top] per visible series, by kind (bars and areas stack separately)
  const spans = new Map<number, [number, number][]>();
  for (const kind of ["bar", "area"] as const) {
    const group = visible.filter((s) => s.kind === kind && !s.right);
    if (stacking && group.length) stack(group, n, !!percent).forEach((sp, k) => spans.set(group[k].i, sp));
  }
  const span = (s: (typeof visible)[number], c: number): [number, number] => spans.get(s.i)?.[c] ?? [0, s.values[c]];
  const extent = (right: boolean) => {
    const vs = visible.filter((s) => s.right === right).flatMap((s) => categories.map((_, c) => span(s, c)).flat());
    const finite = vs.filter(Number.isFinite);
    if (!right && Number.isFinite(reference)) finite.push(reference as number);
    const includeZero = !!props.zero || visible.some((s) => s.right === right && s.kind !== "line");
    const lo = Math.min(...finite, ...(includeZero ? [0] : []));
    const hi = Math.max(...finite, ...(includeZero ? [0] : []));
    return finite.length ? niceScale(lo, hi) : niceScale(0, 1);
  };
  const left = extent(false);
  const right = rightUnit ? extent(true) : null;
  const pad = { top: right ? 26 : 12, right: right ? 44 : 12, bottom: 26, left: 44 };
  const iw = Math.max(1, width - pad.left - pad.right);
  const ih = H - pad.top - pad.bottom;
  // bars need bands; lines alone run edge to edge
  const step = hasBars ? iw / n : n > 1 ? iw / (n - 1) : iw;
  const x = (c: number) => pad.left + (hasBars ? step * (c + 0.5) : n > 1 ? step * c : iw / 2);
  const yOf = (scale: { min: number; max: number }) => (v: number) => pad.top + ih - (ih * (v - scale.min)) / (scale.max - scale.min || 1);
  const yl = yOf(left);
  const yr = right ? yOf(right) : yl;
  const y = (s: { right: boolean }) => (s.right ? yr : yl);
  const bars = visible.filter((s) => s.kind === "bar");
  const groupWidth = Math.min(step * 0.72, 56 * Math.max(1, stacking ? 1 : bars.length));
  const barWidth = Math.max(2, stacking ? groupWidth : groupWidth / Math.max(1, bars.length));
  const signed = bars.length === 1 && bars[0].values.some((v) => v < 0) && bars[0].values.some((v) => v > 0);
  const barColor = (s: (typeof visible)[number], v: number) =>
    signed ? (better ? (goodness(v, better) === "bad" ? "var(--destructive)" : "var(--success)") : v < 0 ? color(1) : color(0)) : color(s.i);
  const widest = categories.reduce((m, c) => Math.max(m, textWidth(c)), 0);
  const every = Math.max(1, Math.ceil((widest + 10) / Math.max(1, step)));
  const labels = bars.length === (stacking ? bars.length : 1) && bars.length > 0 && !percent && n <= 12 && step >= 28;
  const markers = !hasBars && n <= 24;
  const at = cursor.at;
  const shown = (v: number, s?: { right: boolean }) => (percent && !s?.right ? `${formatNumber(Math.round(v * 10) / 10)}%` : value(v, s?.right ? rightUnit : unit, decimals));
  const markOf = (s: (typeof visible)[number]) => {
    const vs = s.values;
    const finite = vs.map((v, c) => ({ v, c })).filter((p) => Number.isFinite(p.v));
    if (!finite.length || s.kind === "bar") return [] as number[];
    const out = new Set<number>();
    if (marks.includes("max")) out.add(finite.reduce((a, b) => (b.v > a.v ? b : a)).c);
    if (marks.includes("min")) out.add(finite.reduce((a, b) => (b.v < a.v ? b : a)).c);
    if (marks.includes("last")) out.add(finite[finite.length - 1].c);
    return [...out];
  };
  return (
    <Frame title={title} unit={percent ? "%" : unit && !rightUnit ? unit : undefined}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={title} tabIndex={0}
          onPointerMove={(e) => {
            const box = e.currentTarget.getBoundingClientRect();
            const pos = e.clientX - box.left - pad.left;
            const c = hasBars ? Math.floor(pos / step) : n > 1 ? Math.round(pos / step) : 0;
            cursor.setAt(Math.min(n - 1, Math.max(0, c)));
          }}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {left.ticks.map((v, i) => (
            <g key={i}>
              <line x1={pad.left} x2={width - pad.right} y1={yl(v)} y2={yl(v)} className={v === 0 ? "gu-zero" : "gu-grid-line"} />
              <text x={pad.left - 8} y={yl(v) + 4} textAnchor="end" className="gu-axis">{tick(v)}</text>
            </g>
          ))}
          {right?.ticks.map((v, i) => <text key={i} x={width - pad.right + 8} y={yr(v) + 4} className="gu-axis">{tick(v)}</text>)}
          {right ? (
            <>
              <text x={pad.left - 8} y={10} textAnchor="end" className="gu-axis">{unit}</text>
              <text x={width - pad.right + 8} y={10} className="gu-axis">{rightUnit}</text>
            </>
          ) : null}
          {categories.map((c, i) => (i % every === 0 && (hasBars || i === n - 1 || n - 1 - i >= every)) || (!hasBars && i === n - 1) ? (
            <text key={i} x={x(i)} y={H - 8} textAnchor={!hasBars && n > 1 && i === 0 ? "start" : !hasBars && n > 1 && i === n - 1 ? "end" : "middle"} className="gu-axis">
              {fit(c, Math.max(36, step * every - 8))}
            </text>
          ) : null)}
          {at != null ? (hasBars
            ? <rect x={pad.left + step * at} y={pad.top} width={step} height={ih} className="gu-hover-band" />
            : <line x1={x(at)} x2={x(at)} y1={pad.top} y2={pad.top + ih} className="gu-cursor" />) : null}
          {visible.filter((s) => s.kind === "area").map((s) => {
            const top: [number, number][] = categories.map((_, c) => [x(c), y(s)(span(s, c)[1])]);
            const base: [number, number][] = categories.map((_, c) => [x(c), y(s)(stacking ? span(s, c)[0] : Math.max(left.min, Math.min(left.max, 0)))]);
            return <path key={`a${s.i}`} d={band(top, base, curve)} fill={color(s.i)} opacity={stacking ? 0.75 : 0.16} />;
          })}
          {bars.map((s, k) =>
            categories.map((_, c) => {
              const [b, t] = span(s, c);
              if (!Number.isFinite(t)) return null;
              const x0 = x(c) - groupWidth / 2 + (stacking ? 0 : k * barWidth);
              const y0 = y(s)(Math.max(b, t));
              const h = Math.max(1, Math.abs(y(s)(b) - y(s)(t)));
              return <rect key={`b${s.i}-${c}`} x={x0 + 0.5} y={y0} width={barWidth - 1} height={h} rx={Math.min(3, barWidth / 3, h)} fill={barColor(s, s.values[c])} className={at != null && at !== c ? "gu-dim" : undefined} />;
            }),
          )}
          {labels ? categories.map((_, c) => {
            const top = stacking ? Math.max(...bars.map((s) => span(s, c)[1])) : bars[0].values[c];
            const total = stacking ? bars.reduce((a, s) => a + (Number.isFinite(s.values[c]) ? s.values[c] : 0), 0) : top;
            if (!Number.isFinite(top)) return null;
            const neg = total < 0;
            return <text key={`l${c}`} x={x(c)} y={yl(stacking ? (neg ? Math.min(...bars.map((s) => span(s, c)[1])) : top) : total) + (neg ? 14 : -5)} textAnchor="middle" className="gu-value">{formatNumber(total, decimals)}</text>;
          }) : null}
          {visible.filter((s) => s.kind !== "bar").map((s) => {
            const pts: [number, number][] = categories.map((_, c) => [x(c), stacking && s.kind === "area" ? y(s)(span(s, c)[1]) : Number.isFinite(s.values[c]) ? y(s)(s.values[c]) : NaN]);
            return (
              <g key={`p${s.i}`}>
                <path d={path(pts, curve)} fill="none" stroke={color(s.i)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.kind === "line" && s.i % 2 && !hasBars ? "6 4" : undefined} />
                {pts.map(([px, py], c) => (Number.isFinite(py) && (markers || c === at) ? (
                  <circle key={c} cx={px} cy={py} r={c === at ? 4.5 : 2.5} fill={c === at ? "var(--popover)" : color(s.i)} stroke={color(s.i)} strokeWidth={c === at ? 2 : 0} />
                ) : null))}
                {markOf(s).map((c) => (
                  <text key={`m${c}`} x={pts[c][0]} y={pts[c][1] - 9} textAnchor={c === 0 ? "start" : c === n - 1 ? "end" : "middle"} className="gu-value">{formatNumber(stacking ? span(s, c)[1] - span(s, c)[0] : s.values[c], decimals)}</text>
                ))}
              </g>
            );
          })}
          {Number.isFinite(reference) ? (
            <g>
              <line x1={pad.left} x2={width - pad.right} y1={yl(reference!)} y2={yl(reference!)} className="gu-reference" />
              <text x={width - pad.right - 4} y={yl(reference!) - 5} textAnchor="end" className="gu-reference-label">{referenceLabel ? `${referenceLabel} ${formatNumber(reference, decimals)}` : formatNumber(reference, decimals)}</text>
            </g>
          ) : null}
        </svg>
        {at != null && categories[at] != null ? (
          <Readout x={x(at)} y={pad.top} flip={x(at) > width * 0.6} title={categories[at]}
            rows={visible.map((s) => ({ name: s.name, value: shown(stacking && !s.right ? span(s, at)[1] - span(s, at)[0] : s.values[at], s), color: color(s.i) }))} />
        ) : null}
      </div>
      <Legend items={indexed.map((s) => ({ name: s.name, color: color(s.i) }))} hidden={hidden} onToggle={toggle} />
      <SrTable title={title} head={["", ...all.map((s) => s.name)]} rows={categories.map((c, r) => [c, ...all.map((s) => formatNumber(s.values[r], decimals))])} />
    </Frame>
  );
}

/** Horizontal bars: long category names, rankings, signed deviations. */
export function HBarChart({ sid, title, categories: raw, series: rawSeries, unit, decimals, stacked, percent, better, reference, referenceLabel }: { sid?: string; title: string; categories: string[]; series: SeriesData[]; unit?: string; decimals?: number; stacked?: boolean; percent?: boolean; better?: string; reference?: number; referenceLabel?: string }) {
  const { categories, layers } = clip(raw, [{ kind: "bar", series: rawSeries }]);
  const series = layers[0].series;
  const { hidden, toggle } = useHidden(`legend:${sid ?? title}`);
  const visible = series.map((s, i) => ({ ...s, i })).filter((s) => !hidden.has(s.i));
  const stacking = stacked || percent;
  const n = Math.max(1, categories.length);
  const spans = stacking ? stack(visible, n, !!percent) : null;
  const span = (k: number, c: number): [number, number] => spans?.[k][c] ?? [0, visible[k].values[c]];
  const vs = visible.flatMap((_, k) => categories.flatMap((__, c) => span(k, c))).filter(Number.isFinite);
  if (Number.isFinite(reference)) vs.push(reference!);
  const scale = niceScale(Math.min(0, ...vs), Math.max(0, ...vs));
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(categories.length);
  const labelRoom = Math.min(140, Math.max(48, ...categories.map((c) => textWidth(c))) + 16);
  const single = visible.length === 1 || stacking;
  const valueRoom = single && !percent ? 56 : 12;
  const pad = { top: 4, right: valueRoom, bottom: 24, left: labelRoom };
  const rowH = Math.max(24, (stacking ? 1 : visible.length) * 12 + 12);
  const height = Math.max(96, n * rowH + pad.top + pad.bottom);
  const iw = Math.max(1, width - pad.left - pad.right);
  const len = (v: number) => (iw * (v - scale.min)) / (scale.max - scale.min || 1);
  const zero = len(0);
  const bar = Math.min(rowH * 0.72, 24) / (stacking ? 1 : Math.max(1, visible.length));
  const signed = visible.length === 1 && visible[0].values.some((v) => v < 0) && visible[0].values.some((v) => v > 0);
  const fill = (s: { i: number }, v: number) => (signed ? (better ? (goodness(v, better) === "bad" ? "var(--destructive)" : "var(--success)") : v < 0 ? color(1) : color(0)) : color(s.i));
  const at = cursor.at;
  const shown = (v: number) => (percent ? `${formatNumber(Math.round(v * 10) / 10)}%` : value(v, unit, decimals));
  return (
    <Frame title={title} unit={percent ? "%" : unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title} tabIndex={0}
          onPointerMove={(e) => {
            const c = Math.floor((e.clientY - e.currentTarget.getBoundingClientRect().top - pad.top) / rowH);
            cursor.setAt(c >= 0 && c < n ? c : null);
          }}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {scale.ticks.map((v, i) => (
            <g key={i}>
              <line x1={pad.left + len(v)} x2={pad.left + len(v)} y1={pad.top} y2={height - pad.bottom} className={v === 0 ? "gu-zero" : "gu-grid-line"} />
              <text x={pad.left + len(v)} y={height - 6} textAnchor="middle" className="gu-axis">{tick(v)}</text>
            </g>
          ))}
          {at != null ? <rect x={0} y={pad.top + rowH * at} width={width} height={rowH} className="gu-hover-band" /> : null}
          {categories.map((c, ci) => {
            const mid = pad.top + rowH * (ci + 0.5);
            const total = visible.reduce((a, s) => a + (Number.isFinite(s.values[ci]) ? s.values[ci] : 0), 0);
            const end = stacking ? Math.max(...visible.map((_, k) => span(k, ci)[1]), 0) : visible[0]?.values[ci];
            return (
              <g key={ci} className={at != null && at !== ci ? "gu-dim" : undefined}>
                <text x={pad.left - 8} y={mid + 4} textAnchor="end" className="gu-axis gu-axis-label">{fit(c, labelRoom - 12)}</text>
                {visible.map((s, k) => {
                  const [b, t] = span(k, ci);
                  if (!Number.isFinite(t)) return null;
                  const y0 = stacking ? mid - bar / 2 : mid - (bar * visible.length) / 2 + k * bar;
                  const x0 = pad.left + Math.min(len(b), len(t));
                  return <rect key={s.i} x={x0} y={y0 + 0.5} width={Math.max(1, Math.abs(len(t) - len(b)))} height={bar - 1} rx={Math.min(3, bar / 3)} fill={fill(s, s.values[ci])} />;
                })}
                {single && !percent && Number.isFinite(end) ? (
                  <text x={pad.left + (total < 0 && !stacking ? Math.min(len(total), zero) - 6 : Math.max(len(stacking ? end : total), zero) + 6)} y={mid + 4} textAnchor={total < 0 && !stacking ? "end" : "start"} className="gu-value">
                    {formatNumber(stacking ? total : visible[0].values[ci], decimals)}
                  </text>
                ) : null}
              </g>
            );
          })}
          {Number.isFinite(reference) ? (
            <g>
              <line x1={pad.left + len(reference!)} x2={pad.left + len(reference!)} y1={pad.top} y2={height - pad.bottom} className="gu-reference" />
              {referenceLabel ? <text x={pad.left + len(reference!) + 4} y={pad.top + 10} className="gu-reference-label">{referenceLabel}</text> : null}
            </g>
          ) : null}
        </svg>
        {at != null && categories[at] != null && !single ? (
          <Readout x={pad.left + iw * 0.5} y={pad.top + rowH * at} flip={false} title={categories[at]}
            rows={visible.map((s, k) => ({ name: s.name, value: shown(stacking ? span(k, at)[1] - span(k, at)[0] : s.values[at]), color: color(s.i) }))} />
        ) : null}
      </div>
      <Legend items={series.map((s, i) => ({ name: s.name, color: color(i) }))} hidden={hidden} onToggle={toggle} />
      <SrTable title={title} head={["", ...series.map((s) => s.name)]} rows={categories.map((c, r) => [c, ...series.map((s) => formatNumber(s.values[r], decimals))])} />
    </Frame>
  );
}

export function PieChartView({ title, labels, values, unit, decimals }: { title: string; labels: string[]; values: number[]; unit?: string; decimals?: number }) {
  const id = useId();
  const [at, setAt] = useState<number | null>(null);
  const items = labels.slice(0, 12).map((l, i) => ({ label: l, value: Math.max(0, Number(values[i]) || 0) }));
  const total = items.reduce((a, b) => a + b.value, 0);
  let angle = -Math.PI / 2;
  const r = 80;
  const c = 100;
  const focus = at != null ? items[at] : null;
  return (
    <Frame title={title} unit={unit}>
      <div className="gu-pie">
        <svg viewBox="0 0 200 200" className="gu-pie-svg" role="img" aria-labelledby={id} onPointerLeave={() => setAt(null)}>
          <title id={id}>{title}</title>
          {items.map((it, i) => {
            const sweep = total ? (it.value / total) * Math.PI * 2 : 0;
            const a0 = angle;
            angle += sweep;
            const cls = at != null && at !== i ? "gu-dim" : undefined;
            if (sweep >= Math.PI * 2 - 1e-6) return <circle key={i} cx={c} cy={c} r={r} fill={color(i)} className={cls} onPointerEnter={() => setAt(i)} />;
            if (sweep <= 0) return null;
            const large = sweep > Math.PI ? 1 : 0;
            const d = `M${c},${c} L${c + r * Math.cos(a0)},${c + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${c + r * Math.cos(angle)},${c + r * Math.sin(angle)} Z`;
            return <path key={i} d={d} fill={color(i)} stroke="var(--background)" strokeWidth={2} className={cls} onPointerEnter={() => setAt(i)} />;
          })}
          <circle cx={c} cy={c} r={50} fill="var(--background)" />
          <text x={c} y={c - 2} textAnchor="middle" className="gu-pie-value">{focus ? formatShare(focus.value, total) : formatNumber(total, decimals)}</text>
          <text x={c} y={c + 16} textAnchor="middle" className="gu-axis">{focus ? fit(focus.label, 84) : "合计"}</text>
        </svg>
        <ul className="gu-pie-legend">
          {items.map((it, i) => (
            <li key={i} className={at != null && at !== i ? "gu-dim" : undefined} tabIndex={0} onPointerEnter={() => setAt(i)} onPointerLeave={() => setAt(null)} onFocus={() => setAt(i)} onBlur={() => setAt(null)}>
              <span className="gu-swatch" style={{ background: color(i) }} />
              <span className="gu-pie-label">{it.label}</span>
              <span className="gu-pie-num">{formatNumber(it.value, decimals)}</span>
              <span className="gu-pie-pct">{formatShare(it.value, total)}</span>
            </li>
          ))}
        </ul>
      </div>
    </Frame>
  );
}

/** A small trend line inside a Metric; no axes, the last point marked. */
export function Sparkline({ values, better }: { values: number[]; better?: string }) {
  const v = values.filter(Number.isFinite).slice(-60);
  if (v.length < 2) return null;
  const w = 96;
  const h = 28;
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  const pts: [number, number][] = v.map((x, i) => [2 + (i * (w - 4)) / (v.length - 1), h - 3 - ((x - lo) / (hi - lo || 1)) * (h - 6)]);
  const trend = goodness(v[v.length - 1] - v[0], better);
  const stroke = trend === "bad" ? "var(--destructive)" : trend === "good" ? "var(--success)" : "var(--muted-foreground)";
  return (
    <svg className="gu-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={path(pts)} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2.5} fill={stroke} />
    </svg>
  );
}
