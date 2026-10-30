// Markdown fallback of an evaluated tree: copy, search, share/print failure, unsupported clients (D-G8). Derived
// numbers come from data.ts, the same functions the components draw with, so a copy says what the page showed.
import {
  box, changeRate, factValue, facts, formatNumber, formatRate, formatShare, formatSigned, funnel, histogram, isNode, lineDiff, numbers,
  objects, pathTree, pieData, ranking, seriesFrom, seriesOf, strings, tableRows, text, treeOf, waterfall, winners, withUnit, type PathNode, type TreeNode,
} from "./data.ts";
import { MAX_TABLE_ROWS } from "./limits.ts";

type P = Record<string, unknown>;
const cell = (v: unknown, decimals?: unknown) => (typeof v === "boolean" ? (v ? "✓" : "✗") : formatNumber(v, decimals)).replace(/\|/g, "\\|").replace(/\n/g, " ");
// an object here is a binding nothing could resolve: it shows as nothing, never as [object Object]
const plain = (v: unknown) => (v == null || typeof v === "object" ? "" : formatNumber(v));
const titled = (title: unknown, body: string) => [text(title) ? `**${text(title)}**` : "", body].filter(Boolean).join("\n\n");

export function table(columns: string[], rows: unknown[][], decimals?: unknown): string {
  if (!columns.length) return "";
  const head = `| ${columns.map((c) => cell(c)).join(" | ")} |\n| ${columns.map(() => "---").join(" | ")} |`;
  return [head, ...rows.slice(0, MAX_TABLE_ROWS).map((r) => `| ${columns.map((_, i) => cell(r?.[i], decimals)).join(" | ")} |`)].join("\n");
}

function seriesTable(title: unknown, categories: unknown, list: unknown, decimals?: unknown): string {
  const series = seriesOf(list, categories);
  if (!series) return "";
  const rows = (categories as unknown[]).map((c, i) => [c, ...series.map((s) => s.values[i])]);
  return titled(title, table(["", ...series.map((s) => s.name)], rows, decimals));
}

const bullets = (items: string[]) => items.map((t) => `- ${t}`).join("\n");
const treeLines = (n: TreeNode, depth = 0): string[] => [`${"  ".repeat(depth)}- ${n.name}${n.note ? `：${n.note}` : ""}`, ...n.children.flatMap((c) => treeLines(c, depth + 1))];
const pathLines = (nodes: PathNode[], depth = 0): string[] => nodes.flatMap((n) => [`${"  ".repeat(depth)}${n.name}${n.dir ? "/" : ""}`, ...pathLines(n.children, depth + 1)]);

/** An evaluated tree as Markdown. A control's value is still its `$variable` binding there; `read` gives the
 * variable's current value. */
export function nodeToMarkdown(value: unknown, read: (name: string) => unknown = () => undefined): string {
  if (Array.isArray(value)) return value.map((v) => nodeToMarkdown(v, read)).filter((t) => t.trim()).join("\n\n");
  if (!isNode(value)) return typeof value === "string" ? value : "";
  const p: P = value.props || {};
  const o: P = p.opts && typeof p.opts === "object" ? (p.opts as P) : {};
  const kids = (v: unknown) => nodeToMarkdown(v, read);
  const bound = (v: unknown) => (v && typeof v === "object" && typeof (v as { target?: unknown }).target === "string" ? read((v as { target: string }).target) : v);
  const unit = o.unit;
  const d = o.decimals;
  switch (value.typeName) {
    case "Stack":
    case "Grid":
      return kids(p.children);
    case "Card":
      return titled(o.title, kids(p.children));
    case "Details":
      return titled(p.title, kids(p.children));
    case "Divider":
      return p.label ? `---\n\n**${text(p.label)}**` : "---";
    case "Heading":
      return `### ${text(p.text)}`;
    case "Text":
      return text(p.text);
    case "Badge":
      return `\`${text(p.text)}\``;
    case "Callout":
      return `> ${o.title ? `**${text(o.title)}** ` : ""}${text(p.text)}`;
    case "Quote":
      return `> ${text(p.text)}${o.source ? `\n>\n> —— ${text(o.source)}` : ""}`;
    case "List": {
      const items = strings(p.items, 200) ?? [];
      const mark = o.marker === "check" ? "✓ " : o.marker === "cross" ? "✗ " : "";
      return items.map((t, i) => `${o.ordered ? `${i + 1}.` : "-"} ${mark}${t}`).join("\n");
    }
    case "KeyValue": {
      const items = objects<{ label: unknown; value: unknown; unit?: unknown }>(p.items, ["label"]) ?? [];
      return bullets(items.map((it) => `${text(it.label)}：${withUnit(cell(it.value, d), it.unit)}`));
    }
    case "Code":
      return titled(o.title, "```" + text(o.language) + "\n" + text(p.code) + "\n```");
    case "Metric": {
      const rate = changeRate(p.value, o.previous) ?? (typeof o.change === "number" && Number.isFinite(o.change) ? o.change : null);
      return `- ${text(p.label)}：${withUnit(formatNumber(p.value, d), unit)}${rate !== null ? `（${formatRate(rate)}）` : ""}`;
    }
    case "Progress": {
      const max = typeof o.max === "number" && o.max > 0 ? o.max : 100;
      const v = typeof p.value === "number" ? p.value : 0;
      return `- ${text(p.label)}：${max === 100 && !unit ? `${formatNumber(v, d)}%` : `${withUnit(`${formatNumber(v, d)} / ${formatNumber(max, d)}`, unit)}（${formatShare(v, max)}）`}`;
    }
    case "Gauge":
      return `- ${text(p.label)}：${withUnit(formatNumber(p.value, d), unit)}`;
    case "ShareBar":
    case "PieChart": {
      if (!pieData(p.labels, p.values)) return "";
      const values = p.values as number[];
      const total = values.reduce((a, b) => a + b, 0);
      return titled(p.title, table(["", unit ? `数值（${text(unit)}）` : "数值", "占比"], (p.labels as unknown[]).map((l, i) => [l, values[i], formatShare(values[i], total)]), d));
    }
    case "Highlights": {
      const labels = strings(p.labels);
      const values = numbers(p.values);
      if (!labels || !values) return "";
      return titled(o.title, bullets(facts(labels, values, Array.isArray(p.facts) ? p.facts : []).map((f) => `${f.label}${f.at ? `（${f.at}）` : ""}：${factValue(f, unit, d)}`)));
    }
    case "LineChart":
    case "AreaChart":
    case "BarChart":
    case "RadarChart":
      return seriesTable(p.title, value.typeName === "RadarChart" ? p.axes : p.categories, p.series, d);
    case "ComboChart":
      return seriesTable(p.title, p.categories, [...(Array.isArray(p.bars) ? p.bars : []), ...(Array.isArray(p.lines) ? p.lines : [])], d);
    case "ScatterChart": {
      const points = objects<{ name?: unknown; x: number; y: number; size?: number; group?: unknown }>(p.points, ["x", "y"]) ?? [];
      const cols = ["", text(o.xLabel) || "x", text(o.yLabel) || "y", ...(points.some((pt) => pt.size != null) ? ["大小"] : [])];
      return titled(p.title, table(cols, points.map((pt) => [text(pt.name) || text(pt.group), pt.x, pt.y, ...(cols.length > 3 ? [pt.size ?? null] : [])]), d));
    }
    case "Histogram": {
      const bins = histogram(numbers(p.values, 5000) ?? [], o.bins);
      return titled(p.title, table(["区间", "个数"], bins.map((b) => [`${formatNumber(b.from)}–${formatNumber(b.to)}`, b.n])));
    }
    case "BoxPlot": {
      const groups = seriesOf(p.groups) ?? [];
      const rows = groups.map((g) => ({ g, b: box(g.values) })).filter((x) => x.b).map(({ g, b }) => [g.name, b!.min, b!.q1, b!.median, b!.q3, b!.max, b!.n]);
      return titled(p.title, table(["", "最小", "下四分位", "中位数", "上四分位", "最大", "样本数"], rows, d));
    }
    case "Heatmap": {
      const cols = strings(p.columns) ?? [];
      const rows = strings(p.rows) ?? [];
      const values = Array.isArray(p.values) ? p.values : [];
      return titled(p.title, table(["", ...cols], rows.map((r, i) => [r, ...cols.map((_, j) => (Array.isArray(values[i]) ? values[i][j] : null))]), d));
    }
    case "Waterfall": {
      const labels = strings(p.labels);
      const changes = numbers(p.changes);
      if (!labels || !changes) return "";
      const steps = waterfall(labels, changes, o.start, o.startLabel, o.endLabel);
      return titled(p.title, table(["", "变化", "累计"], steps.map((s) => [s.label, s.kind === "start" || s.kind === "end" ? null : formatSigned(s.to - s.from, d), s.to]), d));
    }
    case "Funnel": {
      const stages = strings(p.stages);
      const values = numbers(p.values);
      if (!stages || !values) return "";
      return titled(p.title, table(["阶段", "数量", "上一级转化", "总转化"], funnel(values).map((s, i) => [stages[i] ?? "", s.value, s.step == null ? null : `${formatNumber(Math.round(s.step * 10) / 10)}%`, s.overall == null ? null : `${formatNumber(Math.round(s.overall * 10) / 10)}%`]), d));
    }
    case "Treemap": {
      const items = objects<{ name: unknown; value: number; group?: unknown }>(p.items, ["name", "value"]) ?? [];
      const total = items.reduce((a, b) => a + (Number(b.value) || 0), 0);
      return titled(p.title, table(["", "数值", "占比"], items.map((it) => [text(it.name), it.value, formatShare(Number(it.value) || 0, total)]), d));
    }
    case "Sankey": {
      const links = objects<{ from: unknown; to: unknown; value: number }>(p.links, ["from", "to", "value"]) ?? [];
      return titled(p.title, table(["来源", "去向", "数量"], links.map((l) => [text(l.from), text(l.to), l.value]), d));
    }
    case "Candlestick": {
      const dates = strings(p.dates) ?? [];
      const prices = Array.isArray(p.prices) ? p.prices : [];
      return titled(p.title, table(["日期", "开", "高", "低", "收"], dates.map((t, i) => [t, ...(Array.isArray(prices[i]) ? prices[i].slice(0, 4) : [])]), d));
    }
    case "Dumbbell": {
      const cats = strings(p.categories) ?? [];
      const from = seriesFrom(p.from);
      const to = seriesFrom(p.to);
      if (!from || !to) return "";
      return titled(p.title, table(["", from.name, to.name, "差值"], cats.map((c, i) => [c, from.values[i], to.values[i], Number.isFinite(to.values[i] - from.values[i]) ? formatSigned(to.values[i] - from.values[i], d) : null]), d));
    }
    case "Timeline": {
      const events = objects<{ date: unknown; title: unknown; text?: unknown }>(p.events, ["title"]) ?? [];
      return bullets(events.map((e) => `**${text(e.date)}** ${text(e.title)}${e.text ? `：${text(e.text)}` : ""}`));
    }
    case "Steps": {
      const list = objects<{ title: unknown; text?: unknown }>(p.steps, ["title"]) ?? [];
      return list.map((s, i) => `${i + 1}. ${text(s.title)}${s.text ? `：${text(s.text)}` : ""}`).join("\n");
    }
    case "Checklist": {
      const items = objects<{ text: unknown; done?: unknown; note?: unknown }>(p.items, ["text"]) ?? [];
      return titled(o.title, items.map((it) => `- [${it.done === true ? "x" : " "}] ${text(it.text)}${it.note ? `（${text(it.note)}）` : ""}`).join("\n"));
    }
    case "Gantt": {
      const tasks = objects<{ name: unknown; start: unknown; end: unknown }>(p.tasks, ["name"]) ?? [];
      return titled(p.title, table(["任务", "开始", "结束"], tasks.map((t) => [text(t.name), text(t.start), text(t.end)])));
    }
    case "Table": {
      const columns = Array.isArray(p.columns) ? p.columns.map(text) : [];
      const rows = tableRows(p.rows, columns.length);
      return rows ? table(columns, rows, d) : "";
    }
    case "Compare": {
      const options = strings(p.options) ?? [];
      const rows = objects<{ label: unknown; values: unknown[]; better?: unknown; unit?: unknown }>(p.rows, ["label", "values"]) ?? [];
      const body = rows.map((r) => {
        const best = winners(r.values, r.better);
        return [withUnit(text(r.label), r.unit ? `（${text(r.unit)}）` : ""), ...options.map((_, i) => cell(r.values[i], d) + (best.has(i) ? " ★" : ""))];
      });
      return [table(["", ...options], body), o.recommend ? `推荐：${text(o.recommend)}` : ""].filter(Boolean).join("\n\n");
    }
    case "Ranking": {
      const items = objects<{ name: unknown; value: number; note?: unknown }>(p.items, ["name", "value"]) ?? [];
      return titled(p.title, ranking(items.filter((it) => typeof it.value === "number"), o.better, o.top).map((it) => `${it.rank}. ${text(it.name)}：${withUnit(formatNumber(it.value, d), unit)}${it.note ? `（${text(it.note)}）` : ""}`).join("\n"));
    }
    case "ProsCons":
      return titled(o.title, [`**${text(o.prosLabel) || "优点"}**\n${bullets(strings(p.pros, 50) ?? [])}`, `**${text(o.consLabel) || "缺点"}**\n${bullets(strings(p.cons, 50) ?? [])}`].join("\n\n"));
    case "EntityCard": {
      const fields = objects<{ label: unknown; value: unknown; unit?: unknown }>(o.fields, ["label"]) ?? [];
      const head = `**${text(p.name)}**${o.subtitle ? ` · ${text(o.subtitle)}` : ""}${o.badge ? ` \`${text(o.badge)}\`` : ""}`;
      const price = typeof o.price === "number" ? `价格：${withUnit(formatNumber(o.price, d), o.priceUnit)}` : "";
      const rating = typeof o.rating === "number" ? `评分：${formatNumber(o.rating)} / 5` : "";
      return [head, text(o.description), bullets([price, rating, ...fields.map((f) => `${text(f.label)}：${withUnit(cell(f.value, d), f.unit)}`)].filter(Boolean)), (strings(o.tags, 20) ?? []).map((t) => `\`${t}\``).join(" ")].filter(Boolean).join("\n\n");
    }
    case "Flow": {
      const nodes = objects<{ id: unknown; label: unknown }>(p.nodes, ["id"]) ?? [];
      const name = new Map(nodes.map((n) => [text(n.id), text(n.label) || text(n.id)]));
      const edges = objects<{ from: unknown; to: unknown; label?: unknown }>(p.edges, ["from", "to"]) ?? [];
      return titled(o.title, bullets(edges.map((e) => `${name.get(text(e.from)) ?? text(e.from)} → ${name.get(text(e.to)) ?? text(e.to)}${e.label ? `（${text(e.label)}）` : ""}`)));
    }
    case "Tree": {
      const root = treeOf(p.root);
      return root ? titled(o.title, treeLines(root).join("\n")) : "";
    }
    case "Diff":
      return titled(o.title, "```diff\n" + lineDiff(text(p.before), text(p.after)).map((l) => (l.kind === "add" ? "+ " : l.kind === "del" ? "- " : "  ") + l.text).join("\n") + "\n```");
    case "FileTree":
      return titled(o.title, "```\n" + pathLines(pathTree(strings(p.paths, 500) ?? [])).join("\n") + "\n```");
    case "JsonView":
      return titled(o.title, "```json\n" + (() => { try { return JSON.stringify(p.data, null, 2) ?? ""; } catch { return ""; } })() + "\n```");
    case "Tabs":
      return ((p.tabs as unknown[]) || []).filter(isNode).map((t) => [`#### ${text(t.props?.label)}`, kids(t.props?.children)].join("\n\n")).join("\n\n");
    case "Button":
      return `- ${text(p.label)}`;
    case "Select": {
      const v = bound(p.value);
      return `- ${text(p.label)}：${Array.isArray(v) ? v.map(plain).join("、") : plain(v)}`;
    }
    case "Slider":
      return `- ${text(p.label)}：${withUnit(plain(bound(p.value)), o.unit)}`;
    case "Input":
      return `- ${text(p.label)}：${plain(bound(p.value)) || plain(o.placeholder)}`;
    case "Toggle":
      return `- ${text(p.label)}：${bound(p.value) === true ? "开" : "关"}`;
    case "HtmlArtifact":
      return titled(p.title, text(p.fallbackText));
    default:
      return "";
  }
}

/** Component names used in a parsed tree, for telemetry and history placeholders. */
export function componentsOf(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) value.forEach((v) => componentsOf(v, out));
  else if (isNode(value)) {
    if (value.typeName && value.typeName !== "Series" && value.typeName !== "Tab") out.push(value.typeName);
    Object.values(value.props || {}).forEach((v) => componentsOf(v, out));
  }
  return out;
}

