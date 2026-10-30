// What the components compute, in one place without React: the renderers, the Markdown fallbacks (copy, search,
// print) and the eval checker all call these, so what is copied is what was shown. A model passes raw data; shares,
// ranks, changes, totals, bins and winners are derived here (the P6 failures were the model doing this arithmetic).
//
// Every reader of evaluated data checks its shape first: a well-formed program can still compute the wrong shape
// (`rows + [total]` is a number, a row can hold rows), and that is shown as malformed, never as an empty table or a
// shifted chart.
import { MAX_CHART_POINTS, MAX_TABLE_ROWS } from "./limits.ts";

const fmt = new Intl.NumberFormat("zh-CN");
const fixed = [0, 1, 2, 3, 4].map((d) => new Intl.NumberFormat("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const validDecimals = (d: unknown): d is number => Number.isInteger(d) && Number(d) >= 0 && Number(d) <= 4;

/** A number as the reader sees it; `decimals` (0–4) fixes the digits after the point, else up to three show. */
export const formatNumber = (v: unknown, decimals?: unknown) => {
  if (typeof v !== "number" || !Number.isFinite(v)) return v == null || typeof v === "object" ? "" : String(v);
  return (validDecimals(decimals) ? fixed[decimals] : fmt).format(v);
};
/** A rate in %, to one decimal; halves round away from zero both ways (Math.round alone gives +1.3 but -4.2). */
export const formatRate = (rate: number) => `${rate > 0 ? "+" : ""}${formatNumber((Math.sign(rate) * Math.round(Math.abs(rate) * 10)) / 10)}%`;
/** A signed amount: +12 / -3.5. */
export const formatSigned = (v: number, decimals?: unknown) => `${v > 0 ? "+" : ""}${formatNumber(v, decimals)}`;
export const withUnit = (s: string, unit?: unknown) => (s && typeof unit === "string" && unit ? `${s} ${unit}` : s);
/** A share of a whole in %, one decimal. */
export const formatShare = (part: number, whole: number) => `${formatNumber(whole ? Math.round((part / whole) * 1000) / 10 : 0)}%`;

export const text = (v: unknown) => (v == null || typeof v === "object" ? "" : String(v));
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const scalar = (v: unknown) => v == null || typeof v === "string" || typeof v === "boolean" || finite(v);
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** A list of numbers, or null; `max` caps how many are read. */
export const numbers = (v: unknown, max = MAX_CHART_POINTS): number[] | null => (Array.isArray(v) && v.every(finite) ? v.slice(0, max) : null);
export const strings = (v: unknown, max = MAX_CHART_POINTS): string[] | null => (Array.isArray(v) && v.every((s) => s == null || typeof s !== "object") ? v.slice(0, max).map(text) : null);
/** An array of objects that each have the `need`ed keys (a missing optional key is fine). */
export function objects<T = Record<string, unknown>>(v: unknown, need: string[], max = MAX_TABLE_ROWS): T[] | null {
  if (!Array.isArray(v) || !v.every((o) => record(o) && need.every((k) => o[k] != null))) return null;
  return v.slice(0, max) as T[];
}

// ---------- tables and charts ----------

/** Table rows: lists of plain values, none wider than the header. */
export const tableRows = (rows: unknown, width: number): unknown[][] | null =>
  Array.isArray(rows) && rows.every((r) => Array.isArray(r) && r.length <= width && r.every(scalar)) ? rows : null;
/** Chart data: plain labels, and for each series no more numbers than labels. */
export const chartData = (labels: unknown, values: unknown[]): boolean =>
  Array.isArray(labels) && labels.every(scalar) && values.every((v) => Array.isArray(v) && v.length <= labels.length && v.every(scalar));
/** Pie data: one non-negative number per label; a missing share would silently shrink the whole. */
export const pieData = (labels: unknown, values: unknown): boolean =>
  Array.isArray(labels) && labels.every(scalar) && Array.isArray(values) && values.length === labels.length && values.every((v) => finite(v) && v >= 0);

export type SeriesData = { name: string; values: number[] };
type Node = { type?: string; typeName?: string; props?: Record<string, unknown> };
export const isNode = (v: unknown): v is Node => !!v && typeof v === "object" && (v as Node).type === "element";
/** Series nodes as data, or null when one holds something other than numbers (a gap may be null). */
export function seriesOf(list: unknown, categories?: unknown): SeriesData[] | null {
  const nodes = (Array.isArray(list) ? list : [list]).filter(isNode);
  if (!nodes.length && list != null && !(Array.isArray(list) && !list.length)) return null;
  const series = nodes.map((s) => ({ name: text(s.props?.name), values: s.props?.values }));
  if (categories !== undefined ? !chartData(categories, series.map((s) => s.values)) : !series.every((s) => Array.isArray(s.values) && s.values.every(scalar))) return null;
  return series.map((s) => ({ name: s.name, values: (s.values as unknown[]).map((v) => (finite(v) ? v : NaN)) }));
}
export const seriesFrom = (node: unknown) => (isNode(node) ? seriesOf([node]) : null)?.[0] ?? null;

// ---------- metrics ----------

/** Change rate in % from the previous value, or null when it is not defined (no or zero previous). */
export const changeRate = (value: unknown, previous: unknown): number | null =>
  finite(value) && finite(previous) && previous !== 0 ? ((value - previous) / Math.abs(previous)) * 100 : null;
/** Whether a change reads as good: up is good unless `better` is low. */
export const goodness = (delta: number, better?: unknown) => (delta === 0 ? "flat" : (delta > 0) === (better !== "low") ? "good" : "bad");

export type Fact = { key: string; label: string; at?: string; value: number; rate?: number | null; signed?: boolean; percent?: boolean };
const FACT_LABEL: Record<string, string> = { max: "最高", min: "最低", total: "合计", avg: "平均", median: "中位数", range: "极差", change: "首尾变化", rise: "最大增幅", drop: "最大降幅", share: "最大占比" };
/** Names models reach for instead of the documented ones. */
const FACT_ALIAS: Record<string, string> = {
  sum: "total", mean: "avg", average: "avg", maximum: "max", highest: "max", minimum: "min", lowest: "min",
  increase: "rise", growth: "rise", decrease: "drop", decline: "drop", delta: "change", diff: "change", spread: "range",
};
const median = (v: number[]) => {
  const s = [...v].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
/** The facts a Highlights block states, each with the label it belongs to. Unknown or undefined facts are left out. */
export function facts(labels: string[], values: number[], keys: unknown[]): Fact[] {
  const n = Math.min(labels.length, values.length);
  if (!n) return [];
  const v = values.slice(0, n);
  const at = (i: number) => labels[i] ?? "";
  const total = v.reduce((a, b) => a + b, 0);
  const iMax = v.indexOf(Math.max(...v));
  const iMin = v.indexOf(Math.min(...v));
  const steps = v.slice(1).map((x, i) => ({ i: i + 1, d: x - v[i] }));
  const out: Fact[] = [];
  for (const key of new Set(keys.map((k) => FACT_ALIAS[String(k).toLowerCase()] ?? String(k).toLowerCase()))) {
    const label = FACT_LABEL[key];
    if (!label) continue;
    if (key === "max") out.push({ key, label, at: at(iMax), value: v[iMax] });
    else if (key === "min") out.push({ key, label, at: at(iMin), value: v[iMin] });
    else if (key === "total") out.push({ key, label, value: total });
    else if (key === "avg") out.push({ key, label, value: total / n });
    else if (key === "median") out.push({ key, label, value: median(v) });
    else if (key === "range") out.push({ key, label, value: v[iMax] - v[iMin] });
    else if (key === "change" && n > 1) out.push({ key, label, at: `${at(0)} → ${at(n - 1)}`, value: v[n - 1] - v[0], rate: changeRate(v[n - 1], v[0]), signed: true });
    else if (key === "rise" && steps.some((s) => s.d > 0)) {
      const s = steps.reduce((a, b) => (b.d > a.d ? b : a));
      out.push({ key, label, at: at(s.i), value: s.d, rate: changeRate(v[s.i], v[s.i - 1]), signed: true });
    } else if (key === "drop" && steps.some((s) => s.d < 0)) {
      const s = steps.reduce((a, b) => (b.d < a.d ? b : a));
      out.push({ key, label, at: at(s.i), value: s.d, rate: changeRate(v[s.i], v[s.i - 1]), signed: true });
    } else if (key === "share" && total > 0) out.push({ key, label, at: at(iMax), value: (v[iMax] / total) * 100, percent: true });
  }
  return out;
}
export const factValue = (f: Fact, unit?: unknown, decimals?: unknown) =>
  f.percent ? `${formatNumber(Math.round(f.value * 10) / 10)}%` : withUnit(f.signed ? formatSigned(f.value, decimals) : formatNumber(f.value, decimals), unit) + (f.rate != null ? `（${formatRate(f.rate)}）` : "");

// ---------- distributions ----------

export type Box = { min: number; q1: number; median: number; q3: number; max: number; outliers: number[]; n: number };
const quantile = (s: number[], q: number) => {
  const pos = (s.length - 1) * q;
  const lo = Math.floor(pos);
  return s[lo] + (s[Math.min(s.length - 1, lo + 1)] - s[lo]) * (pos - lo);
};
/** Five-number summary with Tukey fences (1.5 IQR); whiskers end at the furthest point inside them. */
export function box(values: number[]): Box | null {
  const s = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!s.length) return null;
  const q1 = quantile(s, 0.25);
  const q3 = quantile(s, 0.75);
  const lo = q1 - 1.5 * (q3 - q1);
  const hi = q3 + 1.5 * (q3 - q1);
  const inside = s.filter((v) => v >= lo && v <= hi);
  return { min: inside[0], q1, median: quantile(s, 0.5), q3, max: inside[inside.length - 1], outliers: s.filter((v) => v < lo || v > hi), n: s.length };
}

/** Equal-width bins on round edges; Sturges' rule when the count is not given. */
export function histogram(values: number[], count?: unknown): { from: number; to: number; n: number }[] {
  const v = values.filter(Number.isFinite);
  if (!v.length) return [];
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  const k = Number.isInteger(count) && Number(count) >= 2 ? Math.min(40, Number(count)) : Math.min(20, Math.max(4, Math.ceil(Math.log2(v.length) + 1)));
  if (hi === lo) return [{ from: lo, to: hi, n: v.length }];
  const raw = (hi - lo) / k;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const width = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((w) => w >= raw) ?? raw;
  const start = Math.floor(lo / width) * width;
  const bins = Array.from({ length: Math.max(1, Math.ceil((hi - start) / width + 1e-9)) }, (_, i) => ({ from: start + i * width, to: start + (i + 1) * width, n: 0 }));
  for (const x of v) bins[Math.min(bins.length - 1, Math.floor((x - start) / width))].n++;
  return bins;
}

/** Least-squares line through the points: y = a + b·x, and r². */
export function regression(points: { x: number; y: number }[]) {
  const n = points.length;
  if (n < 2) return null;
  const mx = points.reduce((a, p) => a + p.x, 0) / n;
  const my = points.reduce((a, p) => a + p.y, 0) / n;
  const sxx = points.reduce((a, p) => a + (p.x - mx) ** 2, 0);
  const syy = points.reduce((a, p) => a + (p.y - my) ** 2, 0);
  const sxy = points.reduce((a, p) => a + (p.x - mx) * (p.y - my), 0);
  if (!sxx) return null;
  const b = sxy / sxx;
  return { a: my - b * mx, b, r2: syy ? (sxy * sxy) / (sxx * syy) : 1 };
}

// ---------- bridges, funnels, rankings, comparisons ----------

export type Step = { label: string; from: number; to: number; kind: "start" | "up" | "down" | "end" };
/** A waterfall's bars: the start, each signed change from the running total, and the end the changes arrive at. */
export function waterfall(labels: string[], changes: number[], start?: unknown, startLabel?: unknown, endLabel?: unknown): Step[] {
  const out: Step[] = [];
  let at = finite(start) ? start : 0;
  if (finite(start)) out.push({ label: text(startLabel) || "起点", from: 0, to: at, kind: "start" });
  changes.forEach((d, i) => {
    out.push({ label: labels[i] ?? "", from: at, to: at + d, kind: d >= 0 ? "up" : "down" });
    at += d;
  });
  out.push({ label: text(endLabel) || "合计", from: 0, to: at, kind: "end" });
  return out;
}

/** Each stage's conversion from the one before and from the first, in %. */
export const funnel = (values: number[]) =>
  values.map((v, i) => ({ value: v, step: i && values[i - 1] ? (v / values[i - 1]) * 100 : null, overall: values[0] ? (v / values[0]) * 100 : null }));

/** Items in rank order: by value, larger first unless `better` is low; ties keep the given order and share a rank. */
export function ranking<T extends { value: number }>(items: T[], better?: unknown, top?: unknown): (T & { rank: number })[] {
  const dir = better === "low" ? 1 : -1;
  const sorted = items.map((it, i) => ({ it, i })).sort((a, b) => dir * (a.it.value - b.it.value) || a.i - b.i);
  const ranked: (T & { rank: number })[] = [];
  sorted.forEach(({ it }, i) => ranked.push({ ...it, rank: i && sorted[i - 1].it.value === it.value ? ranked[i - 1].rank : i + 1 }));
  return Number.isInteger(top) && Number(top) > 0 ? ranked.slice(0, Number(top)) : ranked;
}

/** For a comparison row, the indexes of the best values (all of them on a tie); none unless `better` is set and
 * at least two values are numbers. */
export function winners(values: unknown[], better?: unknown): Set<number> {
  if (better !== "high" && better !== "low") return new Set();
  const nums = values.map((v, i) => ({ v, i })).filter((x): x is { v: number; i: number } => finite(x.v));
  if (nums.length < 2) return new Set();
  const best = better === "low" ? Math.min(...nums.map((x) => x.v)) : Math.max(...nums.map((x) => x.v));
  if (nums.every((x) => x.v === best)) return new Set();
  return new Set(nums.filter((x) => x.v === best).map((x) => x.i));
}

// ---------- text diffs and trees ----------

export type DiffLine = { kind: "same" | "add" | "del"; text: string };
/** Line diff by longest common subsequence; past 400×400 lines it degrades to before-then-after. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  if (a.length * b.length > 160000) return [...a.map((t) => ({ kind: "del" as const, text: t })), ...b.map((t) => ({ kind: "add" as const, text: t }))];
  const dp = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (i < a.length && (j === b.length || dp[i + 1][j] >= dp[i][j + 1])) out.push({ kind: "del", text: a[i++] });
    else out.push({ kind: "add", text: b[j++] });
  }
  return out;
}

export type PathNode = { name: string; dir: boolean; children: PathNode[] };
/** Paths as a tree; a path ending in / is a directory, as is anything with children. Directories sort first. */
export function pathTree(paths: string[]): PathNode[] {
  const root: PathNode = { name: "", dir: true, children: [] };
  for (const path of paths.slice(0, 500)) {
    const parts = path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((part, i) => {
      let next = node.children.find((c) => c.name === part);
      if (!next) node.children.push((next = { name: part, dir: false, children: [] }));
      if (i < parts.length - 1 || path.endsWith("/")) next.dir = true;
      node = next;
    });
  }
  const sort = (n: PathNode) => {
    n.children.sort((x, y) => Number(y.dir) - Number(x.dir) || x.name.localeCompare(y.name));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}

export type TreeNode = { name: string; note?: string; children: TreeNode[] };
/** A Tree's nodes, bounded in depth and count; anything without a name is dropped. */
export function treeOf(value: unknown, depth = 0, budget = { left: 300 }): TreeNode | null {
  if (!record(value) || value.name == null || typeof value.name === "object" || budget.left-- <= 0 || depth > 12) return null;
  const kids = Array.isArray(value.children) ? value.children.map((c) => treeOf(c, depth + 1, budget)).filter((c): c is TreeNode => !!c) : [];
  return { name: text(value.name), note: value.note == null ? undefined : text(value.note), children: kids };
}

/** Parses YYYY-MM-DD (or YYYY-MM, YYYY) to a UTC day number; null otherwise. */
export function dayOf(s: unknown): number | null {
  const m = /^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$/.exec(text(s).trim());
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), Number(m[2] ?? 1) - 1, Number(m[3] ?? 1));
  return Number.isFinite(t) ? Math.round(t / 86400000) : null;
}

// ---------- one shape check per component ----------

const opt = (p: Record<string, unknown>) => (record(p.opts) ? p.opts : {});
const allFinite = (v: unknown, n?: number) => Array.isArray(v) && v.every((x) => x == null || finite(x)) && (n === undefined || v.length >= n);
/** Whether a component's evaluated props have the shape it draws; false shows "数据格式有误". The renderer and the
 * eval checker both decide with this. Components without data (layout, text, controls) always pass. */
export function shapeOk(name: string, p: Record<string, unknown>): boolean {
  switch (name) {
    case "Table":
      return tableRows(p.rows, Array.isArray(p.columns) ? p.columns.length : 0) !== null;
    case "LineChart":
    case "AreaChart":
    case "BarChart":
      return seriesOf(p.series, p.categories) !== null;
    case "ComboChart":
      return seriesOf(p.bars, p.categories) !== null && seriesOf(p.lines, p.categories) !== null;
    case "RadarChart":
      return strings(p.axes) !== null && (p.axes as unknown[]).length >= 3 && seriesOf(p.series, p.axes) !== null;
    case "PieChart":
    case "ShareBar":
      return pieData(p.labels, p.values);
    case "Highlights":
      return strings(p.labels) !== null && numbers(p.values) !== null;
    case "KeyValue":
      return objects(p.items, ["label"]) !== null;
    case "List":
      return strings(p.items) !== null;
    case "Timeline":
      return objects(p.events, ["title"]) !== null;
    case "Steps":
      return objects(p.steps, ["title"]) !== null;
    case "Checklist":
      return objects(p.items, ["text"]) !== null;
    case "Compare":
      return strings(p.options) !== null && (objects<{ values: unknown }>(p.rows, ["label", "values"])?.every((r) => Array.isArray(r.values)) ?? false);
    case "Ranking":
      return objects<{ value: unknown }>(p.items, ["name", "value"])?.every((it) => finite(it.value)) ?? false;
    case "ProsCons":
      return strings(p.pros) !== null && strings(p.cons) !== null;
    case "ScatterChart":
      return objects<{ x: unknown; y: unknown }>(p.points, ["x", "y"])?.every((pt) => finite(pt.x) && finite(pt.y)) ?? false;
    case "Histogram":
      return numbers(p.values, 5000) !== null && (p.values as unknown[]).length > 0;
    case "BoxPlot":
      return seriesOf(p.groups) !== null;
    case "Heatmap":
      return strings(p.columns) !== null && strings(p.rows) !== null && Array.isArray(p.values) && p.values.every((r) => allFinite(r));
    case "Waterfall":
      return strings(p.labels) !== null && numbers(p.changes) !== null;
    case "Funnel":
      return strings(p.stages) !== null && numbers(p.values) !== null && (p.values as unknown[]).length > 0;
    case "Treemap":
      return objects<{ value: unknown }>(p.items, ["name", "value"])?.every((it) => finite(it.value)) ?? false;
    case "Sankey":
      return objects<{ value: unknown }>(p.links, ["from", "to", "value"])?.every((l) => finite(l.value) && l.value > 0) ?? false;
    case "Candlestick":
      return strings(p.dates) !== null && Array.isArray(p.prices) && p.prices.every((r) => Array.isArray(r) && r.length >= 4 && r.slice(0, 4).every(finite));
    case "Dumbbell":
      return strings(p.categories) !== null && seriesFrom(p.from) !== null && seriesFrom(p.to) !== null;
    case "Gantt":
      return objects<{ start: unknown; end: unknown }>(p.tasks, ["name", "start", "end"])?.every((t) => dayOf(t.start) !== null && dayOf(t.end) !== null) ?? false;
    case "Flow":
      return objects(p.nodes, ["id"]) !== null && objects(p.edges, ["from", "to"]) !== null;
    case "Tree":
      return treeOf(p.root) !== null;
    case "FileTree":
      return strings(p.paths) !== null;
    case "EntityCard":
      return opt(p).fields == null || objects(opt(p).fields, ["label"]) !== null;
    default:
      return true;
  }
}
