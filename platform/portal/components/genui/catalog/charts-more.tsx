"use client";

// The less common charts, in their own chunk: loaded with the first block that uses one. Each takes raw data and
// computes what it draws (bins, quartiles, running totals, conversion, layouts) in data.ts or here.
import { useMemo, useState } from "react";
import {
  box, formatNumber, formatShare, formatSigned, funnel, goodness, histogram, numbers, objects, regression, seriesFrom, seriesOf, strings, text, waterfall,
} from "./data";
import { Frame, Legend, Malformed, Readout, SrTable, color, fit, niceScale, textWidth, tick, useCursor, useHidden, useWidth, value } from "./kit";

type P = Record<string, any>;
const o = (p: P): P => p.opts || {};

// ---------- scatter ----------

function ScatterView({ p }: { p: P }) {
  const opt = o(p);
  const points = objects<{ name?: unknown; x: number; y: number; size?: number; group?: unknown }>(p.points, ["x", "y"], 500);
  const { ref, width } = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  if (!points || !points.every((pt) => Number.isFinite(pt.x) && Number.isFinite(pt.y))) return <Malformed />;
  const groups = [...new Set(points.map((pt) => text(pt.group)))];
  const H = 260;
  const yName = [text(opt.yLabel), text(opt.yUnit)].filter(Boolean).join(" · ");
  const sizes = points.map((pt) => (Number.isFinite(pt.size) ? Math.max(0, pt.size!) : NaN));
  const maxSize = Math.max(0, ...sizes.filter(Number.isFinite));
  // the largest bubble sits on an extreme of the data, so the plot leaves its radius free on every side
  const room = maxSize > 0 ? 18 : 6;
  const pad = { top: (yName ? 22 : 8) + room, right: 10 + room, bottom: 34, left: 48 };
  const xs = niceScale(Math.min(...points.map((pt) => pt.x)), Math.max(...points.map((pt) => pt.x)));
  const ys = niceScale(Math.min(...points.map((pt) => pt.y)), Math.max(...points.map((pt) => pt.y)));
  const iw = Math.max(1, width - pad.left - pad.right);
  const ih = H - pad.top - pad.bottom;
  const X = (v: number) => pad.left + (iw * (v - xs.min)) / (xs.max - xs.min || 1);
  const Y = (v: number) => pad.top + ih - (ih * (v - ys.min)) / (ys.max - ys.min || 1);
  const radius = (i: number) => (maxSize > 0 && Number.isFinite(sizes[i]) ? 4 + 14 * Math.sqrt(sizes[i] / maxSize) : 4.5);
  const fitLine = opt.trend === true ? regression(points) : null;
  const labelled = points.length <= 14 && points.some((pt) => pt.name != null);
  const near = (cx: number, cy: number) => {
    let best: number | null = null;
    let dist = 24 * 24;
    points.forEach((pt, i) => {
      const d = (X(pt.x) - cx) ** 2 + (Y(pt.y) - cy) ** 2;
      if (d < dist) [best, dist] = [i, d];
    });
    return best;
  };
  const focus = at != null ? points[at] : null;
  return (
    <Frame title={text(p.title)}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => {
            const b = e.currentTarget.getBoundingClientRect();
            setAt(near(e.clientX - b.left, e.clientY - b.top));
          }}
          onPointerLeave={() => setAt(null)}
          onKeyDown={(e) => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            e.preventDefault();
            setAt((v) => (v == null ? 0 : (v + (e.key === "ArrowRight" ? 1 : points.length - 1)) % points.length));
          }}
          onBlur={() => setAt(null)}>
          {ys.ticks.map((v, i) => (
            <g key={`y${i}`}><line x1={pad.left} x2={width - pad.right} y1={Y(v)} y2={Y(v)} className="gu-grid-line" /><text x={pad.left - 8} y={Y(v) + 4} textAnchor="end" className="gu-axis">{tick(v)}</text></g>
          ))}
          {xs.ticks.map((v, i) => <text key={`x${i}`} x={X(v)} y={H - 18} textAnchor="middle" className="gu-axis">{tick(v)}</text>)}
          <text x={pad.left + iw} y={H - 2} textAnchor="end" className="gu-axis">{[text(opt.xLabel), text(opt.xUnit)].filter(Boolean).join(" · ")}</text>
          <text x={pad.left - 8} y={10} textAnchor="end" className="gu-axis">{yName}</text>
          {fitLine ? <line x1={X(xs.min)} x2={X(xs.max)} y1={Y(fitLine.a + fitLine.b * xs.min)} y2={Y(fitLine.a + fitLine.b * xs.max)} className="gu-reference" /> : null}
          {points.map((pt, i) => (
            <circle key={i} cx={X(pt.x)} cy={Y(pt.y)} r={radius(i)} fill={color(groups.indexOf(text(pt.group)))} fillOpacity={maxSize > 0 ? 0.55 : 0.85}
              stroke={i === at ? "var(--foreground)" : color(groups.indexOf(text(pt.group)))} strokeWidth={i === at ? 2 : 1} className={at != null && at !== i ? "gu-dim" : undefined} />
          ))}
          {labelled ? points.map((pt, i) => (pt.name != null ? <text key={`n${i}`} x={X(pt.x) > width - 60 ? X(pt.x) - radius(i) - 4 : X(pt.x) + radius(i) + 4} y={Y(pt.y) + 4} textAnchor={X(pt.x) > width - 60 ? "end" : "start"} className="gu-axis">{fit(text(pt.name), 96)}</text> : null)) : null}
        </svg>
        {focus ? (
          <Readout x={X(focus.x)} y={Y(focus.y)} flip={X(focus.x) > width * 0.6} title={text(focus.name) || text(focus.group)}
            rows={[
              { name: text(opt.xLabel) || "x", value: value(focus.x, opt.xUnit, opt.decimals) },
              { name: text(opt.yLabel) || "y", value: value(focus.y, opt.yUnit, opt.decimals) },
              ...(Number.isFinite(focus.size) ? [{ name: "大小", value: formatNumber(focus.size, opt.decimals) }] : []),
            ]} />
        ) : null}
      </div>
      {groups.length > 1 ? <Legend items={groups.map((g, i) => ({ name: g || "其它", color: color(i) }))} /> : null}
      {fitLine ? <div className="gu-caption">趋势线 y = {formatNumber(Math.round(fitLine.b * 1000) / 1000)}x {fitLine.a >= 0 ? "+" : "−"} {formatNumber(Math.abs(Math.round(fitLine.a * 100) / 100))}，R² = {formatNumber(Math.round(fitLine.r2 * 100) / 100)}</div> : null}
      <SrTable title={text(p.title)} head={["", text(opt.xLabel) || "x", text(opt.yLabel) || "y"]} rows={points.map((pt) => [text(pt.name) || text(pt.group), formatNumber(pt.x), formatNumber(pt.y)])} />
    </Frame>
  );
}

// ---------- radar ----------

function RadarView({ p, sid }: { p: P; sid?: string }) {
  const opt = o(p);
  const axes = strings(p.axes, 12);
  const series = seriesOf(p.series, p.axes);
  const { hidden, toggle } = useHidden(`legend:${sid ?? text(p.title)}`);
  const [at, setAt] = useState<number | null>(null);
  if (!axes || axes.length < 3 || !series) return <Malformed />;
  const max = typeof opt.max === "number" && opt.max > 0 ? opt.max : niceScale(0, Math.max(...series.flatMap((s) => s.values).filter(Number.isFinite), 1)).max;
  const n = axes.length;
  const c = 130;
  const r = 92;
  const pt = (k: number, v: number): [number, number] => {
    const a = -Math.PI / 2 + (k * 2 * Math.PI) / n;
    const t = Math.max(0, Math.min(1, v / max));
    return [c + r * t * Math.cos(a), c + r * t * Math.sin(a)];
  };
  const visible = series.map((s, i) => ({ ...s, i })).filter((s) => !hidden.has(s.i));
  return (
    <Frame title={text(p.title)}>
      <div className="gu-plot gu-radar">
        <svg viewBox="0 0 260 260" role="img" aria-label={text(p.title)} onPointerLeave={() => setAt(null)}>
          {[0.25, 0.5, 0.75, 1].map((t) => <polygon key={t} points={axes.map((_, k) => pt(k, t * max).join(",")).join(" ")} className="gu-radar-ring" />)}
          {axes.map((a, k) => {
            const [x, y] = pt(k, max * 1.16);
            const [ex, ey] = pt(k, max);
            return (
              <g key={k}>
                <line x1={c} y1={c} x2={ex} y2={ey} className="gu-grid-line" />
                <text x={x} y={y + 4} textAnchor={Math.abs(x - c) < 8 ? "middle" : x > c ? "start" : "end"} className="gu-axis">{fit(a, 70)}</text>
              </g>
            );
          })}
          {visible.map((s) => (
            <polygon key={s.i} points={s.values.slice(0, n).map((v, k) => pt(k, Number.isFinite(v) ? v : 0).join(",")).join(" ")} fill={color(s.i)} fillOpacity={at === s.i ? 0.32 : visible.length > 1 ? 0.08 : 0.16}
              stroke={color(s.i)} strokeWidth={2} strokeLinejoin="round" className={at != null && at !== s.i ? "gu-dim" : undefined} onPointerEnter={() => setAt(s.i)} />
          ))}
        </svg>
        {at != null && series[at] ? <Readout x={8} y={8} flip={false} title={series[at].name} rows={axes.map((a, k) => ({ name: a, value: formatNumber(series[at].values[k], opt.decimals) }))} /> : null}
      </div>
      <Legend items={series.map((s, i) => ({ name: s.name, color: color(i) }))} hidden={hidden} onToggle={toggle} />
      <SrTable title={text(p.title)} head={["", ...series.map((s) => s.name)]} rows={axes.map((a, k) => [a, ...series.map((s) => formatNumber(s.values[k], opt.decimals))])} />
    </Frame>
  );
}

// ---------- histogram and box plot ----------

function HistogramView({ p }: { p: P }) {
  const opt = o(p);
  const values = numbers(p.values, 5000);
  const bins = useMemo(() => histogram(values ?? [], opt.bins), [values?.join(","), opt.bins]);
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(bins.length);
  if (!values || !bins.length) return <Malformed />;
  const H = 220;
  const pad = { top: 12, right: 12, bottom: 26, left: 40 };
  const ys = niceScale(0, Math.max(...bins.map((b) => b.n)), 4, true);
  const iw = Math.max(1, width - pad.left - pad.right);
  const ih = H - pad.top - pad.bottom;
  const bw = iw / bins.length;
  const Y = (v: number) => pad.top + ih - (ih * v) / (ys.max || 1);
  const edges = [...bins.map((b) => b.from), bins[bins.length - 1].to];
  // label every gap-th bin edge; the last edge too when it clears the label before it
  const gap = Math.ceil(Math.max(1, (textWidth(tick(edges[edges.length - 1])) + 10) / bw));
  const at = cursor.at;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => cursor.setAt(Math.min(bins.length - 1, Math.max(0, Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - pad.left) / bw))))}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {ys.ticks.map((v, i) => <g key={i}><line x1={pad.left} x2={width - pad.right} y1={Y(v)} y2={Y(v)} className={v === 0 ? "gu-zero" : "gu-grid-line"} /><text x={pad.left - 8} y={Y(v) + 4} textAnchor="end" className="gu-axis">{tick(v)}</text></g>)}
          {bins.map((b, i) => (
            <g key={i}>
              <rect x={pad.left + i * bw + 0.5} y={Y(b.n)} width={Math.max(1, bw - 1)} height={Math.max(0, pad.top + ih - Y(b.n))} rx={2} fill={color(0)} className={at != null && at !== i ? "gu-dim" : undefined} />
            </g>
          ))}
          {edges.map((edge, k) => (k % gap === 0 || (k === bins.length && (k % gap) * bw >= textWidth(tick(edge)) + 10))
            ? <text key={`e${k}`} x={pad.left + k * bw} y={H - 8} textAnchor={k === 0 ? "start" : k === bins.length ? "end" : "middle"} className="gu-axis">{tick(edge)}</text> : null)}
        </svg>
        {at != null ? <Readout x={pad.left + (at + 0.5) * bw} y={pad.top} flip={(at + 0.5) * bw > iw * 0.6} title={`${formatNumber(bins[at].from)} – ${formatNumber(bins[at].to)}${opt.unit ? ` ${text(opt.unit)}` : ""}`} rows={[{ name: "个数", value: formatNumber(bins[at].n) }, { name: "占比", value: formatShare(bins[at].n, values.length) }]} /> : null}
      </div>
      <div className="gu-caption">共 {formatNumber(values.length)} 个值</div>
      <SrTable title={text(p.title)} head={["区间", "个数"]} rows={bins.map((b) => [`${formatNumber(b.from)}–${formatNumber(b.to)}`, b.n])} />
    </Frame>
  );
}

function BoxPlotView({ p }: { p: P }) {
  const opt = o(p);
  const groups = seriesOf(p.groups);
  const stats = useMemo(() => (groups ?? []).map((g) => ({ name: g.name, b: box(g.values) })).filter((x) => x.b), [JSON.stringify(groups)]);
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(stats.length);
  if (!groups || !stats.length) return <Malformed />;
  const labelRoom = Math.min(120, Math.max(40, ...stats.map((s) => textWidth(s.name))) + 16);
  const rowH = 40;
  const pad = { top: 6, right: 16, bottom: 24, left: labelRoom };
  const H = pad.top + pad.bottom + rowH * stats.length;
  const all = stats.flatMap((s) => [s.b!.min, s.b!.max, ...s.b!.outliers]);
  const xs = niceScale(Math.min(...all), Math.max(...all));
  const iw = Math.max(1, width - pad.left - pad.right);
  const X = (v: number) => pad.left + (iw * (v - xs.min)) / (xs.max - xs.min || 1);
  const at = cursor.at;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => { const r = Math.floor((e.clientY - e.currentTarget.getBoundingClientRect().top - pad.top) / rowH); cursor.setAt(r >= 0 && r < stats.length ? r : null); }}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {xs.ticks.map((v, i) => <g key={i}><line x1={X(v)} x2={X(v)} y1={pad.top} y2={H - pad.bottom} className="gu-grid-line" /><text x={X(v)} y={H - 6} textAnchor="middle" className="gu-axis">{tick(v)}</text></g>)}
          {stats.map((s, i) => {
            const b = s.b!;
            const mid = pad.top + rowH * (i + 0.5);
            return (
              <g key={i} className={at != null && at !== i ? "gu-dim" : undefined}>
                <text x={pad.left - 8} y={mid + 4} textAnchor="end" className="gu-axis gu-axis-label">{fit(s.name, labelRoom - 12)}</text>
                <line x1={X(b.min)} x2={X(b.max)} y1={mid} y2={mid} stroke={color(i)} strokeWidth={1.5} />
                <line x1={X(b.min)} x2={X(b.min)} y1={mid - 6} y2={mid + 6} stroke={color(i)} strokeWidth={1.5} />
                <line x1={X(b.max)} x2={X(b.max)} y1={mid - 6} y2={mid + 6} stroke={color(i)} strokeWidth={1.5} />
                <rect x={X(b.q1)} y={mid - 11} width={Math.max(1, X(b.q3) - X(b.q1))} height={22} rx={3} fill={color(i)} fillOpacity={0.18} stroke={color(i)} strokeWidth={1.5} />
                <line x1={X(b.median)} x2={X(b.median)} y1={mid - 11} y2={mid + 11} stroke={color(i)} strokeWidth={2.5} />
                {b.outliers.map((v, k) => <circle key={k} cx={X(v)} cy={mid} r={2.5} fill="none" stroke={color(i)} />)}
              </g>
            );
          })}
        </svg>
        {at != null ? (
          <Readout x={X(stats[at].b!.median)} y={pad.top + rowH * at} flip={X(stats[at].b!.median) > width * 0.6} title={stats[at].name}
            rows={[["中位数", stats[at].b!.median], ["四分位", null], ["最小", stats[at].b!.min], ["最大", stats[at].b!.max]].map(([name, v]) => ({
              name: String(name),
              value: v === null ? `${formatNumber(stats[at].b!.q1, opt.decimals)} – ${formatNumber(stats[at].b!.q3, opt.decimals)}` : value(v as number, opt.unit, opt.decimals),
            }))} />
        ) : null}
      </div>
      <SrTable title={text(p.title)} head={["", "最小", "下四分位", "中位数", "上四分位", "最大"]} rows={stats.map((s) => [s.name, ...[s.b!.min, s.b!.q1, s.b!.median, s.b!.q3, s.b!.max].map((v) => formatNumber(v, opt.decimals))])} />
    </Frame>
  );
}

// ---------- heatmap ----------

function HeatmapView({ p }: { p: P }) {
  const opt = o(p);
  const cols = strings(p.columns, 60);
  const rows = strings(p.rows, 60);
  const values = Array.isArray(p.values) && p.values.every((r: unknown) => Array.isArray(r) && r.every((v) => v == null || Number.isFinite(v))) ? (p.values as number[][]) : null;
  const { ref, width } = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<[number, number] | null>(null);
  if (!cols || !rows || !values) return <Malformed />;
  const flat = values.slice(0, rows.length).flatMap((r) => r.slice(0, cols.length)).filter((v) => Number.isFinite(v));
  const lo = Math.min(...flat);
  const hi = Math.max(...flat);
  const labelRoom = Math.min(110, Math.max(36, ...rows.map((r) => textWidth(r))) + 16);
  const cell = Math.max(22, Math.min(64, (width - labelRoom) / Math.max(1, cols.length)));
  const W = labelRoom + cell * cols.length;
  const head = 24;
  const H = head + rows.length * Math.min(cell, 34);
  const ch = Math.min(cell, 34);
  const shade = (v: number) => {
    let t = hi === lo ? 1 : (v - lo) / (hi - lo);
    if (opt.better === "low") t = 1 - t;
    return `color-mix(in srgb, var(${opt.better ? "--success" : "--series-1"}) ${Math.round(8 + t * 82)}%, var(--background))`;
  };
  const show = cell >= 34;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot gu-scroll-x">
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={text(p.title)} onPointerLeave={() => setAt(null)}>
          {cols.map((c, j) => <text key={j} x={labelRoom + cell * (j + 0.5)} y={16} textAnchor="middle" className="gu-axis">{fit(c, cell - 4)}</text>)}
          {rows.map((r, i) => (
            <g key={i}>
              <text x={labelRoom - 8} y={head + ch * (i + 0.5) + 4} textAnchor="end" className="gu-axis gu-axis-label">{fit(r, labelRoom - 12)}</text>
              {cols.map((_, j) => {
                const v = values[i]?.[j];
                if (!Number.isFinite(v)) return null;
                const strong = (hi === lo ? 1 : (v - lo) / (hi - lo)) > 0.55 !== (opt.better === "low");
                return (
                  <g key={j} onPointerEnter={() => setAt([i, j])}>
                    <rect x={labelRoom + cell * j + 1} y={head + ch * i + 1} width={cell - 2} height={ch - 2} rx={3} fill={shade(v)} className={at && (at[0] !== i || at[1] !== j) ? undefined : undefined} stroke={at && at[0] === i && at[1] === j ? "var(--foreground)" : "none"} />
                    {show ? <text x={labelRoom + cell * (j + 0.5)} y={head + ch * (i + 0.5) + 4} textAnchor="middle" className={strong ? "gu-cell-strong" : "gu-cell"}>{tick(v)}</text> : null}
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
        {at ? <Readout x={labelRoom + cell * (at[1] + 0.5)} y={head + ch * at[0]} flip={labelRoom + cell * (at[1] + 0.5) > width * 0.6} title={`${rows[at[0]]} · ${cols[at[1]]}`} rows={[{ name: "数值", value: value(values[at[0]][at[1]], opt.unit, opt.decimals) }]} /> : null}
      </div>
      <div className="gu-heat-scale" aria-hidden="true"><span>{formatNumber(lo, opt.decimals)}</span><i style={{ background: `linear-gradient(to right, ${shade(opt.better === "low" ? hi : lo)}, ${shade(opt.better === "low" ? lo : hi)})` }} /><span>{formatNumber(hi, opt.decimals)}</span></div>
      <SrTable title={text(p.title)} head={["", ...cols]} rows={rows.map((r, i) => [r, ...cols.map((_, j) => formatNumber(values[i]?.[j], opt.decimals))])} />
    </Frame>
  );
}

// ---------- waterfall and funnel ----------

function WaterfallView({ p }: { p: P }) {
  const opt = o(p);
  const labels = strings(p.labels);
  const changes = numbers(p.changes);
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor((labels?.length ?? 0) + 2);
  if (!labels || !changes) return <Malformed />;
  const steps = waterfall(labels, changes, opt.start, opt.startLabel, opt.endLabel);
  const H = 240;
  const pad = { top: 18, right: 8, bottom: 26, left: 44 };
  const ys = niceScale(Math.min(0, ...steps.flatMap((s) => [s.from, s.to])), Math.max(0, ...steps.flatMap((s) => [s.from, s.to])));
  const iw = Math.max(1, width - pad.left - pad.right);
  const ih = H - pad.top - pad.bottom;
  const bw = iw / steps.length;
  const Y = (v: number) => pad.top + ih - (ih * (v - ys.min)) / (ys.max - ys.min || 1);
  const fill = (k: string) => (k === "up" ? "var(--success)" : k === "down" ? "var(--destructive)" : color(0));
  const widest = Math.max(...steps.map((s) => textWidth(s.label)));
  const every = Math.max(1, Math.ceil((widest + 8) / bw));
  const at = cursor.at;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => cursor.setAt(Math.min(steps.length - 1, Math.max(0, Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - pad.left) / bw))))}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {ys.ticks.map((v, i) => <g key={i}><line x1={pad.left} x2={width - pad.right} y1={Y(v)} y2={Y(v)} className={v === 0 ? "gu-zero" : "gu-grid-line"} /><text x={pad.left - 8} y={Y(v) + 4} textAnchor="end" className="gu-axis">{tick(v)}</text></g>)}
          {steps.map((s, i) => {
            const x0 = pad.left + bw * i + bw * 0.18;
            const w = bw * 0.64;
            const top = Y(Math.max(s.from, s.to));
            const total = s.kind === "start" || s.kind === "end";
            return (
              <g key={i} className={at != null && at !== i ? "gu-dim" : undefined}>
                <rect x={x0} y={top} width={w} height={Math.max(1, Math.abs(Y(s.from) - Y(s.to)))} rx={2} fill={fill(s.kind)} />
                {i < steps.length - 1 ? <line x1={x0 + w} x2={x0 + bw} y1={Y(s.to)} y2={Y(s.to)} className="gu-cursor" /> : null}
                <text x={x0 + w / 2} y={top - 5} textAnchor="middle" className="gu-value">{total ? formatNumber(s.to, opt.decimals) : formatSigned(s.to - s.from, opt.decimals)}</text>
                {i % every === 0 || total ? <text x={x0 + w / 2} y={H - 8} textAnchor="middle" className="gu-axis">{fit(s.label, bw * every - 6)}</text> : null}
              </g>
            );
          })}
        </svg>
        {at != null && steps[at] ? <Readout x={pad.left + bw * (at + 0.5)} y={pad.top} flip={bw * (at + 0.5) > iw * 0.6} title={steps[at].label}
          rows={steps[at].kind === "start" || steps[at].kind === "end" ? [{ name: "数值", value: value(steps[at].to, opt.unit, opt.decimals) }] : [{ name: "变化", value: `${formatSigned(steps[at].to - steps[at].from, opt.decimals)}${opt.unit ? ` ${text(opt.unit)}` : ""}` }, { name: "累计", value: value(steps[at].to, opt.unit, opt.decimals) }]} /> : null}
      </div>
      <SrTable title={text(p.title)} head={["", "变化", "累计"]} rows={steps.map((s) => [s.label, s.kind === "start" || s.kind === "end" ? "" : formatSigned(s.to - s.from, opt.decimals), formatNumber(s.to, opt.decimals)])} />
    </Frame>
  );
}

function FunnelView({ p }: { p: P }) {
  const opt = o(p);
  const stages = strings(p.stages);
  const values = numbers(p.values);
  if (!stages || !values || !values.length) return <Malformed />;
  const rows = funnel(values);
  const max = Math.max(...values, 0) || 1;
  const pct = (v: number | null) => (v == null ? "" : `${formatNumber(Math.round(v * 10) / 10)}%`);
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <ol className="gu-funnel">
        {rows.map((r, i) => (
          <li key={i}>
            <span className="gu-funnel-label">{stages[i] ?? ""}</span>
            <span className="gu-funnel-bar"><i style={{ width: `${Math.max(2, (r.value / max) * 100)}%`, background: color(0), opacity: 1 - i * (0.5 / Math.max(1, rows.length - 1)) }} /></span>
            <span className="gu-funnel-value">{formatNumber(r.value, opt.decimals)}</span>
            <span className="gu-funnel-rate">{i ? <>{pct(r.step)}<span className="gu-faint"> · 总 {pct(r.overall)}</span></> : null}</span>
          </li>
        ))}
      </ol>
    </Frame>
  );
}

// ---------- treemap ----------

type Rect = { x: number; y: number; w: number; h: number };
/** Squarified treemap (Bruls et al.): rows laid along the shorter side, keeping cells close to square. */
function squarify(values: number[], box: Rect): Rect[] {
  const total = values.reduce((a, b) => a + b, 0);
  const out: Rect[] = new Array(values.length);
  if (!total) return values.map(() => ({ x: box.x, y: box.y, w: 0, h: 0 }));
  const order = values.map((v, i) => ({ v: (v / total) * box.w * box.h, i })).sort((a, b) => b.v - a.v);
  let rect = { ...box };
  let row: typeof order = [];
  const worst = (r: typeof order, side: number) => {
    const s = r.reduce((a, b) => a + b.v, 0);
    const mx = Math.max(...r.map((x) => x.v));
    const mn = Math.min(...r.map((x) => x.v));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  const lay = (r: typeof order) => {
    const s = r.reduce((a, b) => a + b.v, 0);
    const horizontal = rect.w >= rect.h;
    const side = horizontal ? rect.h : rect.w;
    const thick = s / side;
    let at = 0;
    for (const x of r) {
      const len = x.v / thick;
      out[x.i] = horizontal ? { x: rect.x, y: rect.y + at, w: thick, h: len } : { x: rect.x + at, y: rect.y, w: len, h: thick };
      at += len;
    }
    rect = horizontal ? { x: rect.x + thick, y: rect.y, w: rect.w - thick, h: rect.h } : { x: rect.x, y: rect.y + thick, w: rect.w, h: rect.h - thick };
  };
  for (const item of order) {
    const side = Math.min(rect.w, rect.h);
    if (!row.length || worst([...row, item], side) <= worst(row, side)) row.push(item);
    else {
      lay(row);
      row = [item];
    }
  }
  if (row.length) lay(row);
  return out;
}

function TreemapView({ p }: { p: P }) {
  const opt = o(p);
  const items = objects<{ name: unknown; value: number; group?: unknown }>(p.items, ["name", "value"], 200);
  const { ref, width } = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  if (!items || !items.every((it) => Number.isFinite(it.value))) return <Malformed />;
  const list = items.filter((it) => it.value > 0);
  const H = Math.round(Math.max(200, Math.min(360, width * 0.56)));
  const rects = squarify(list.map((it) => it.value), { x: 0, y: 0, w: width, h: H });
  const groups = [...new Set(list.map((it) => text(it.group)))];
  const total = list.reduce((a, b) => a + b.value, 0);
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} onPointerLeave={() => setAt(null)}>
          {list.map((it, i) => {
            const r = rects[i];
            const name = text(it.name);
            const room = r.w - 10;
            const fits = r.h >= 22 && room >= 24;
            const fill = color(groups.length > 1 ? groups.indexOf(text(it.group)) : i);
            return (
              <g key={i} onPointerEnter={() => setAt(i)} className={at != null && at !== i ? "gu-dim" : undefined}>
                <rect x={r.x + 1} y={r.y + 1} width={Math.max(0, r.w - 2)} height={Math.max(0, r.h - 2)} rx={3} fill={fill} fillOpacity={0.85} />
                {fits ? <text x={r.x + 6} y={r.y + 17} className="gu-tree-label">{fit(name, room)}</text> : null}
                {fits && r.h >= 40 ? <text x={r.x + 6} y={r.y + 33} className="gu-tree-value">{fit(formatShare(it.value, total), room)}</text> : null}
              </g>
            );
          })}
        </svg>
        {at != null && list[at] ? <Readout x={rects[at].x + rects[at].w / 2} y={rects[at].y + 8} flip={rects[at].x + rects[at].w / 2 > width * 0.6} title={text(list[at].name)} rows={[{ name: "数值", value: value(list[at].value, opt.unit, opt.decimals) }, { name: "占比", value: formatShare(list[at].value, total) }]} /> : null}
      </div>
      {groups.length > 1 ? <Legend items={groups.map((g, i) => ({ name: g || "其它", color: color(i) }))} /> : null}
      <SrTable title={text(p.title)} head={["", "数值", "占比"]} rows={list.map((it) => [text(it.name), formatNumber(it.value, opt.decimals), formatShare(it.value, total)])} />
    </Frame>
  );
}

// ---------- sankey ----------

function SankeyView({ p }: { p: P }) {
  const opt = o(p);
  const links = objects<{ from: unknown; to: unknown; value: number }>(p.links, ["from", "to", "value"], 120);
  const { ref, width } = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const layout = useMemo(() => {
    if (!links || !links.every((l) => Number.isFinite(l.value) && l.value > 0)) return null;
    const ls = links.map((l) => ({ from: text(l.from), to: text(l.to), value: l.value })).filter((l) => l.from !== l.to);
    const names = [...new Set(ls.flatMap((l) => [l.from, l.to]))];
    // layer = longest path from a source; an edge back to an earlier layer (a cycle) is ignored for layering
    const layer = new Map(names.map((n) => [n, 0]));
    for (let pass = 0; pass < names.length; pass++) {
      let moved = false;
      for (const l of ls) if (layer.get(l.to)! < layer.get(l.from)! + 1 && layer.get(l.from)! + 1 < names.length) { layer.set(l.to, layer.get(l.from)! + 1); moved = true; }
      if (!moved) break;
    }
    const depth = Math.max(...layer.values()) + 1;
    const size = new Map(names.map((n) => [n, Math.max(ls.filter((l) => l.from === n).reduce((a, l) => a + l.value, 0), ls.filter((l) => l.to === n).reduce((a, l) => a + l.value, 0))]));
    const columns = Array.from({ length: depth }, (_, d) => names.filter((n) => layer.get(n) === d));
    return { ls, names, layer, depth, size, columns };
  }, [JSON.stringify(links)]);
  if (!layout || !layout.ls.length) return <Malformed />;
  const { ls, layer, depth, size, columns } = layout;
  const H = Math.max(220, Math.max(...columns.map((c) => c.length)) * 44);
  const nodeW = 10;
  const labelRoom = Math.min(110, Math.max(...layout.names.map((n) => textWidth(n))) + 16);
  const iw = Math.max(1, width - labelRoom * 2);
  const gap = 10;
  const scale = Math.min(...columns.map((c) => (H - gap * (c.length - 1)) / (c.reduce((a, n) => a + size.get(n)!, 0) || 1)));
  const pos = new Map<string, { x: number; y: number; h: number; outAt: number; inAt: number }>();
  columns.forEach((c, d) => {
    const used = c.reduce((a, n) => a + size.get(n)! * scale, 0) + gap * (c.length - 1);
    let y = (H - used) / 2;
    for (const n of c) {
      const h = Math.max(2, size.get(n)! * scale);
      pos.set(n, { x: labelRoom + (depth > 1 ? (iw - nodeW) * (d / (depth - 1)) : iw / 2), y, h, outAt: y, inAt: y });
      y += h + gap;
    }
  });
  const sorted = ls.map((l, i) => ({ ...l, i })).sort((a, b) => pos.get(a.from)!.y - pos.get(b.from)!.y || pos.get(a.to)!.y - pos.get(b.to)!.y);
  const bands = sorted.map((l) => {
    const s = pos.get(l.from)!;
    const t = pos.get(l.to)!;
    const h = l.value * scale;
    const y0 = s.outAt + h / 2;
    const y1 = t.inAt + h / 2;
    s.outAt += h;
    t.inAt += h;
    const x0 = s.x + nodeW;
    const x1 = t.x;
    const mx = (x0 + x1) / 2;
    return { ...l, d: `M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}`, h, back: layer.get(l.to)! <= layer.get(l.from)! };
  });
  const nodeIndex = new Map(layout.names.map((n, i) => [n, i]));
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} onPointerLeave={() => setAt(null)}>
          {bands.filter((b) => !b.back).map((b) => (
            <path key={b.i} d={b.d} fill="none" stroke={color(nodeIndex.get(b.from)!)} strokeOpacity={at === b.i ? 0.55 : 0.28} strokeWidth={Math.max(1, b.h)} onPointerEnter={() => setAt(b.i)} />
          ))}
          {[...pos.entries()].map(([n, r]) => {
            const d = layer.get(n)!;
            const leftSide = d < depth - 1 || depth === 1;
            return (
              <g key={n}>
                <rect x={r.x} y={r.y} width={nodeW} height={r.h} rx={2} fill={color(nodeIndex.get(n)!)} />
                <text x={leftSide && d === 0 ? r.x - 6 : leftSide ? r.x + nodeW + 6 : r.x - 6} y={r.y + r.h / 2 + 4} textAnchor={d === 0 || !leftSide ? "end" : "start"} className="gu-axis gu-axis-label">{fit(n, labelRoom - 12)}</text>
              </g>
            );
          })}
        </svg>
        {at != null ? <Readout x={width / 2} y={8} flip={false} title={`${ls[at].from} → ${ls[at].to}`} rows={[{ name: "数量", value: value(ls[at].value, opt.unit, opt.decimals) }, { name: "占来源", value: formatShare(ls[at].value, size.get(ls[at].from)!) }]} /> : null}
      </div>
      <SrTable title={text(p.title)} head={["来源", "去向", "数量"]} rows={ls.map((l) => [l.from, l.to, formatNumber(l.value, opt.decimals)])} />
    </Frame>
  );
}

// ---------- candlestick and dumbbell ----------

function CandlestickView({ p }: { p: P }) {
  const opt = o(p);
  const dates = strings(p.dates, 400);
  const prices = Array.isArray(p.prices) && p.prices.every((r: unknown) => Array.isArray(r) && r.length >= 4 && r.slice(0, 4).every(Number.isFinite)) ? (p.prices as number[][]) : null;
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(Math.min(dates?.length ?? 0, prices?.length ?? 0));
  if (!dates || !prices) return <Malformed />;
  const n = Math.min(dates.length, prices.length);
  const H = 240;
  const pad = { top: 12, right: 8, bottom: 26, left: 48 };
  const ys = niceScale(Math.min(...prices.slice(0, n).map((r) => r[2])), Math.max(...prices.slice(0, n).map((r) => r[1])));
  const iw = Math.max(1, width - pad.left - pad.right);
  const ih = H - pad.top - pad.bottom;
  const bw = iw / Math.max(1, n);
  const Y = (v: number) => pad.top + ih - (ih * (v - ys.min)) / (ys.max - ys.min || 1);
  const greenUp = opt.rise === "green";
  const tint = (up: boolean) => (up === !greenUp ? "var(--destructive)" : "var(--success)");
  const every = Math.max(1, Math.ceil((Math.max(...dates.slice(0, n).map((d) => textWidth(d))) + 10) / bw));
  const at = cursor.at;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => cursor.setAt(Math.min(n - 1, Math.max(0, Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - pad.left) / bw))))}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {ys.ticks.map((v, i) => <g key={i}><line x1={pad.left} x2={width - pad.right} y1={Y(v)} y2={Y(v)} className="gu-grid-line" /><text x={pad.left - 8} y={Y(v) + 4} textAnchor="end" className="gu-axis">{tick(v)}</text></g>)}
          {at != null ? <rect x={pad.left + bw * at} y={pad.top} width={bw} height={ih} className="gu-hover-band" /> : null}
          {prices.slice(0, n).map(([op, hi, lo, cl], i) => {
            const cx = pad.left + bw * (i + 0.5);
            const up = cl >= op;
            const w = Math.max(1, Math.min(14, bw * 0.62));
            return (
              <g key={i} stroke={tint(up)} fill={tint(up)}>
                <line x1={cx} x2={cx} y1={Y(hi)} y2={Y(lo)} strokeWidth={1} />
                <rect x={cx - w / 2} y={Y(Math.max(op, cl))} width={w} height={Math.max(1, Math.abs(Y(op) - Y(cl)))} strokeWidth={0} />
                {i % every === 0 ? <text x={cx} y={H - 8} textAnchor="middle" className="gu-axis" stroke="none">{fit(dates[i], bw * every - 4)}</text> : null}
              </g>
            );
          })}
        </svg>
        {at != null ? (() => {
          const [op, hi, lo, cl] = prices[at];
          const prev = at ? prices[at - 1][3] : null;
          return <Readout x={pad.left + bw * (at + 0.5)} y={pad.top} flip={bw * (at + 0.5) > iw * 0.6} title={dates[at]}
            rows={[["开", op], ["高", hi], ["低", lo], ["收", cl]].map(([name, v]) => ({ name: String(name), value: formatNumber(v, opt.decimals) })).concat(prev ? [{ name: "涨跌", value: `${formatSigned(Math.round(((cl - prev) / prev) * 1000) / 10)}%` }] : [])} />;
        })() : null}
      </div>
      <SrTable title={text(p.title)} head={["日期", "开", "高", "低", "收"]} rows={prices.slice(0, n).map((r, i) => [dates[i], ...r.slice(0, 4).map((v) => formatNumber(v, opt.decimals))])} />
    </Frame>
  );
}

function DumbbellView({ p }: { p: P }) {
  const opt = o(p);
  const cats = strings(p.categories, 60);
  const from = seriesFrom(p.from);
  const to = seriesFrom(p.to);
  const { ref, width } = useWidth<HTMLDivElement>();
  const cursor = useCursor(cats?.length ?? 0);
  if (!cats || !from || !to) return <Malformed />;
  const all = [...from.values, ...to.values].filter(Number.isFinite);
  if (!all.length) return <Malformed />;
  const xs = niceScale(Math.min(...all), Math.max(...all));
  const labelRoom = Math.min(130, Math.max(44, ...cats.map((c) => textWidth(c))) + 16);
  const diffRoom = 64;
  const rowH = 30;
  const pad = { top: 4, right: diffRoom, bottom: 24, left: labelRoom };
  const H = pad.top + pad.bottom + rowH * cats.length;
  const iw = Math.max(1, width - pad.left - pad.right);
  const X = (v: number) => pad.left + (iw * (v - xs.min)) / (xs.max - xs.min || 1);
  const at = cursor.at;
  return (
    <Frame title={text(p.title)} unit={opt.unit}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title)} tabIndex={0}
          onPointerMove={(e) => { const r = Math.floor((e.clientY - e.currentTarget.getBoundingClientRect().top - pad.top) / rowH); cursor.setAt(r >= 0 && r < cats.length ? r : null); }}
          onPointerLeave={() => cursor.setAt(null)} onKeyDown={cursor.onKeyDown} onBlur={cursor.onBlur}>
          {xs.ticks.map((v, i) => <g key={i}><line x1={X(v)} x2={X(v)} y1={pad.top} y2={H - pad.bottom} className="gu-grid-line" /><text x={X(v)} y={H - 6} textAnchor="middle" className="gu-axis">{tick(v)}</text></g>)}
          {at != null ? <rect x={0} y={pad.top + rowH * at} width={width} height={rowH} className="gu-hover-band" /> : null}
          {cats.map((c, i) => {
            const a = from.values[i];
            const b = to.values[i];
            const mid = pad.top + rowH * (i + 0.5);
            const ok = Number.isFinite(a) && Number.isFinite(b);
            const d = ok ? b - a : NaN;
            return (
              <g key={i}>
                <text x={pad.left - 8} y={mid + 4} textAnchor="end" className="gu-axis gu-axis-label">{fit(c, labelRoom - 12)}</text>
                {ok ? <line x1={X(a)} x2={X(b)} y1={mid} y2={mid} stroke="var(--border-strong)" strokeWidth={3} strokeLinecap="round" /> : null}
                {Number.isFinite(a) ? <circle cx={X(a)} cy={mid} r={5} fill="var(--popover)" stroke={color(0)} strokeWidth={2} /> : null}
                {Number.isFinite(b) ? <circle cx={X(b)} cy={mid} r={5} fill={color(1)} /> : null}
                {ok ? <text x={width - 4} y={mid + 4} textAnchor="end" className="gu-value" data-sign={opt.better ? goodness(d, opt.better) : undefined}>{formatSigned(d, opt.decimals)}</text> : null}
              </g>
            );
          })}
        </svg>
        {at != null ? <Readout x={X(Math.max(from.values[at] || 0, to.values[at] || 0))} y={pad.top + rowH * at} flip={X(Math.max(from.values[at] || 0, to.values[at] || 0)) > width * 0.6} title={cats[at]}
          rows={[{ name: from.name, value: value(from.values[at], opt.unit, opt.decimals), color: color(0) }, { name: to.name, value: value(to.values[at], opt.unit, opt.decimals), color: color(1) }]} /> : null}
      </div>
      <Legend items={[{ name: from.name, color: color(0) }, { name: to.name, color: color(1) }]} />
      <SrTable title={text(p.title)} head={["", from.name, to.name, "差值"]} rows={cats.map((c, i) => [c, formatNumber(from.values[i], opt.decimals), formatNumber(to.values[i], opt.decimals), Number.isFinite(to.values[i] - from.values[i]) ? formatSigned(to.values[i] - from.values[i], opt.decimals) : ""])} />
    </Frame>
  );
}

const VIEWS: Record<string, (props: { p: P; sid?: string }) => React.ReactNode> = {
  ScatterChart: ScatterView,
  RadarChart: RadarView,
  Histogram: HistogramView,
  BoxPlot: BoxPlotView,
  Heatmap: HeatmapView,
  Waterfall: WaterfallView,
  Funnel: FunnelView,
  Treemap: TreemapView,
  Sankey: SankeyView,
  Candlestick: CandlestickView,
  Dumbbell: DumbbellView,
};

export default function MoreChart({ name, p, sid }: { name: string; p: P; sid?: string }) {
  const View = VIEWS[name];
  return View ? <View p={p} sid={sid} /> : null;
}
