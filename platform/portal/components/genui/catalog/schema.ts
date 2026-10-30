// GenUI catalog v2: names, positional props, descriptions and the per-component notes the model reads, without
// React, so the contract generator (`npm run genui:prompt`) and Markdown fallbacks run in Node. Design:
// docs/architecture/design/2026-10-30-genui-registry-v2.md. Same version: only add optional props, opts keys or
// components; renaming, removing or reordering props needs v3. Only components/genui/** imports @openuidev/*.
//
// Conventions the model relies on: the main data are positional (at most four), everything else is one trailing
// `opts` object whose keys mean the same in every component (unit, decimals, tone, better); data about several
// things is an array of objects with fixed keys; and anything derived (shares, ranks, changes, totals, bins,
// winners) is computed by the component, never passed in.
import { z } from "zod/v4";
import { createLibrary, defineComponent, markReactive, steps } from "@openuidev/lang-core";

import { MAX_FALLBACK_CHARS, MAX_HTML_CHARS, MAX_TABLE_ROWS } from "./limits.ts";

// The Portal's CSP forbids eval; without this zod probes `new Function` and the browser logs a violation per page.
z.config({ jitless: true });

export * from "./limits.ts";
export * from "./data.ts";
export * from "./markdown.ts";

// Children may be any content component, layout included.
// eslint-disable-next-line prefer-const
let Content: z.ZodType;
const children = () => z.array(z.lazy(() => Content));
const str = z.string();
const num = z.number();
const tone = z.enum(["neutral", "success", "warning", "danger", "info"]);
const better = z.enum(["high", "low"]);
const scalar = z.union([str, num, z.boolean(), z.null()]);
// digits after the point, for numbers the reader compares to a given precision ("保留两位小数")
const decimals = z.number().int().min(0).max(4);
const opts = <T extends z.ZodRawShape>(shape: T) => z.object(shape).partial().optional();

// A prop the user changes in place: written as a `$variable`, which any other prop can then read in an expression.
const bound = <T extends z.ZodType>(schema: T): T => {
  markReactive(schema);
  return schema;
};

export const GROUPS = ["布局", "文字", "指标", "图表", "高级图表", "过程", "对比", "图示与代码", "交互", "页面"] as const;
export type Group = (typeof GROUPS)[number];
/** What the model reads about a component beyond its signature: when to pick it, and one line of use. */
export type Meta = { group: Group; notes?: string[]; example?: string; needs?: string[] };
export const META: Record<string, Meta> = {};

const def = <T extends z.ZodObject>(name: string, description: string, props: T, meta: Meta) => {
  META[name] = meta;
  return defineComponent({ name, description, props, component: null as unknown });
};

// ---------- 布局 ----------
export const Stack = def("Stack", "纵向（默认）或横向排列子组件；横向在手机上换行", z.object({
  children: children(),
  opts: opts({ direction: z.enum(["col", "row"]), gap: z.enum(["sm", "md", "lg"]), align: z.enum(["start", "center", "end", "stretch"]) }),
}), { group: "布局", example: 'root = Stack([summary, chart, note])' });
export const Grid = def("Grid", "1–4 列网格，窄屏自动减少列数；适合并列的 Metric、EntityCard 或小图", z.object({
  children: children(),
  opts: opts({ columns: z.number().int().min(1).max(4) }),
}), { group: "布局", example: 'kpis = Grid([m1, m2, m3], {columns: 3})' });
export const Card = def("Card", "有边界的一组内容；不要给每段内容都套 Card", z.object({
  children: children(),
  opts: opts({ title: str, subtitle: str, badge: str, tone }),
}), { group: "布局", example: 'plan = Card([t1, m1], {title: "方案 A", badge: "推荐", tone: "success"})' });
export const Tab = def("Tab", "Tabs 中的一页", z.object({ label: str, children: children() }), { group: "布局" });
export const Tabs = def("Tabs", "把并列的几组内容分页显示", z.object({ tabs: z.array(Tab.ref) }), {
  group: "布局", needs: ["Tab"], example: 'tabs = Tabs([Tab("图表", [chart]), Tab("明细", [table])])',
});
export const Details = def("Details", "可折叠的一段次要内容（明细、方法、假设）；多个连用即手风琴", z.object({
  title: str,
  children: children(),
  opts: opts({ open: z.boolean() }),
}), { group: "布局", example: 'more = Details("计算方法", [Text("按含税价计算")])' });
export const Divider = def("Divider", "分隔线，可带一个小标题", z.object({ label: str.optional() }), { group: "布局" });

// ---------- 文字 ----------
export const Heading = def("Heading", "小节标题", z.object({ text: str, opts: opts({ level: z.number().int().min(2).max(4) }) }), { group: "文字" });
export const Text = def("Text", "一段纯文字", z.object({ text: str, opts: opts({ tone: z.enum(["default", "muted"]) }) }), { group: "文字" });
export const Callout = def("Callout", "需要注意的提示、风险或建议", z.object({ text: str, opts: opts({ tone, title: str }) }), {
  group: "文字", example: 'risk = Callout("价格为 10 月官网报价，可能调整", {tone: "warning", title: "注意"})',
});
export const Badge = def("Badge", "简短状态标记", z.object({ text: str, opts: opts({ tone }) }), { group: "文字" });
export const Quote = def("Quote", "引用原文（论文、公告、条款中的原句）", z.object({ text: str, opts: opts({ source: str }) }), { group: "文字" });
export const List = def("List", "要点列表；marker 为 check/cross 时表示满足或不满足", z.object({
  items: z.array(str),
  opts: opts({ ordered: z.boolean(), marker: z.enum(["dot", "check", "cross", "arrow"]) }),
}), { group: "文字", example: 'needs = List(["支持 SSO", "本地部署"], {marker: "check"})' });
export const KeyValue = def("KeyValue", "一个对象的若干属性（规格、参数、档案）", z.object({
  items: z.array(z.object({ label: str, value: scalar, unit: str.optional() })),
  opts: opts({ columns: z.number().int().min(1).max(3), decimals }),
}), { group: "文字", example: 'spec = KeyValue([{label: "上下文", value: 200000, unit: "token"}, {label: "发布", value: "2026-09"}])' });
export const Code = def("Code", "一段代码或命令，可复制", z.object({ code: str, opts: opts({ language: str, title: str }) }), {
  group: "文字", notes: ["只有代码需要和其它组件放在一起（如 Tabs 分语言）时才用；单独的代码直接写在块外的 Markdown 代码围栏里。"],
});

// ---------- 指标 ----------
export const Metric = def("Metric", "一个关键数值；给出 previous 时组件自己算出并显示变化率", z.object({
  label: str,
  value: z.union([num, str]),
  opts: opts({ unit: str, previous: num, change: num, better, decimals, hint: str, trend: z.array(num) }),
}), {
  group: "指标",
  notes: [
    "有上一期的数值就写 previous，不要自己算变化率；change 只用于资料直接给出的变化率（%），二者不同时写。",
    "better 为 low 时数值下降显示为好（成本、耗时、错误率）。trend 是近几期的数值，显示为迷你走势线。",
  ],
  example: 'm = Metric("6 月销售额", 190, {unit: "万元", previous: 172, trend: [120, 135, 128, 160, 172, 190]})',
});
export const Progress = def("Progress", "完成度或配额用量；显示 value / max 和百分比", z.object({
  label: str,
  value: num,
  opts: opts({ max: num, unit: str, target: num, tone, decimals }),
}), { group: "指标", example: 'p = Progress("已用存储", 38, {max: 50, unit: "GB"})' });
export const Gauge = def("Gauge", "单个数值在一个区间里的位置（得分、利用率、风险值）", z.object({
  label: str,
  value: num,
  opts: opts({ min: num, max: num, unit: str, decimals, better, target: num }),
}), { group: "指标", example: 'g = Gauge("CPU 利用率", 72, {max: 100, unit: "%", better: "low"})' });
export const ShareBar = def("ShareBar", "一个整体的组成，一条百分比条（组件算占比）；比饼图更省地方", z.object({
  title: str,
  labels: z.array(str),
  values: z.array(num),
  opts: opts({ unit: str, decimals }),
}), { group: "指标", example: 'mix = ShareBar("流量来源", ["搜索", "直接", "社交"], [52, 30, 18])' });
export const Highlights = def("Highlights", "从一组数据算出的结论数字：最高、最低、合计、平均、首尾变化、最大增幅等", z.object({
  labels: z.array(str),
  values: z.array(num),
  // names, not an enum: a synonym (sum, mean) maps to its fact instead of invalidating the block
  facts: z.array(str),
  opts: opts({ unit: str, decimals, title: str }),
}), {
  group: "指标",
  notes: [
    "需要写“哪个最高/最低、合计多少、涨了多少、哪一期涨得最多”时用它，由组件计算并标出对应标签，不要在文字里自己算。",
    "facts：max 最高、min 最低、total 合计、avg 平均、median 中位数、range 极差、change 首尾变化（含变化率）、rise 最大单期增幅、drop 最大单期降幅、share 最大项占合计的比例。labels 有先后顺序时才用 change/rise/drop。",
  ],
  example: 'facts = Highlights(months, sales, ["change", "rise", "max"], {unit: "万元"})',
});

// ---------- 图表 ----------
export const Series = def("Series", "图表的一组数据，只作为图表参数使用", z.object({ name: str, values: z.array(num) }), { group: "图表" });
const chartOpts = { unit: str, decimals, reference: num, referenceLabel: str };
export const LineChart = def("LineChart", "随时间或顺序变化的趋势", z.object({
  title: str,
  categories: z.array(str),
  series: z.array(Series.ref),
  opts: opts({ ...chartOpts, curve: z.enum(["linear", "smooth", "step"]), zero: z.boolean(), marks: z.array(z.enum(["max", "min", "last"])) }),
}), {
  group: "图表",
  needs: ["Series"],
  notes: ["纵轴默认按数据范围取值，zero 为 true 时从 0 开始。marks 在图上标出最高、最低或最后一个点的数值。reference 画一条参考线（目标、均值、阈值）。"],
  example: 'trend = LineChart("月销售额", months, [Series("销售额", sales)], {unit: "万元", marks: ["max", "last"]})',
});
export const AreaChart = def("AreaChart", "趋势加总量；stacked 堆叠显示各部分随时间的构成，percent 显示为占比", z.object({
  title: str,
  categories: z.array(str),
  series: z.array(Series.ref),
  opts: opts({ ...chartOpts, stacked: z.boolean(), percent: z.boolean(), curve: z.enum(["linear", "smooth", "step"]) }),
}), { group: "图表", needs: ["Series"], example: 'mix = AreaChart("各渠道用户", months, [Series("网页", web), Series("App", app)], {stacked: true})' });
export const BarChart = def("BarChart", "类别之间的数量对比；有负数时以 0 为中线向两侧分开", z.object({
  title: str,
  categories: z.array(str),
  series: z.array(Series.ref),
  opts: opts({ ...chartOpts, horizontal: z.boolean(), stacked: z.boolean(), percent: z.boolean(), better }),
}), {
  group: "图表",
  needs: ["Series"],
  notes: ["类别名长或超过 8 个时用 horizontal。stacked 堆叠各系列，percent 把每个类别换算成 100%。正负值（超预期/低于预期、盈亏）直接给带符号的数，better 决定正负哪边显示为好。排名用 Ranking。"],
  example: 'gap = BarChart("实际与预期之差", items.name, [Series("差值", items.gap)], {unit: "亿元", horizontal: true})',
});
export const ComboChart = def("ComboChart", "柱和线放在同一张图：数量与比率（如收入和增长率）", z.object({
  title: str,
  categories: z.array(str),
  bars: z.array(Series.ref),
  lines: z.array(Series.ref),
  opts: opts({ unit: str, lineUnit: str, decimals }),
}), {
  group: "图表",
  needs: ["Series"],
  notes: ["lineUnit 与 unit 不同时，线用右侧纵轴。"],
  example: 'rev = ComboChart("收入与增速", years, [Series("收入", revenue)], [Series("同比", growth)], {unit: "亿元", lineUnit: "%"})',
});
export const PieChart = def("PieChart", "同一整体的少量组成部分（不超过 6 类），组件算占比", z.object({
  title: str,
  labels: z.array(str),
  values: z.array(num),
  opts: opts({ unit: str, decimals }),
}), {
  group: "图表",
  notes: ["后两个参数是标签数组和数值数组，不放 Series。"],
  example: 'budget = PieChart("预算构成", items.name, items.amount, {unit: "万元"})',
});

// ---------- 高级图表 ----------
export const ScatterChart = def("ScatterChart", "两个数值指标的关系（价格与得分、规模与效率）；size 字段画成气泡", z.object({
  title: str,
  points: z.array(z.object({ name: str.optional(), x: num, y: num, size: num.optional(), group: str.optional() })),
  opts: opts({ xLabel: str, yLabel: str, xUnit: str, yUnit: str, decimals, trend: z.boolean() }),
}), {
  group: "高级图表",
  notes: ["group 相同的点同色并进入图例。trend 为 true 时画最小二乘趋势线。"],
  example: 'fit = ScatterChart("价格与评分", @Each(models, "m", {name: m.name, x: m.price, y: m.score}), {xLabel: "价格", xUnit: "美元", yLabel: "评分"})',
});
export const RadarChart = def("RadarChart", "几个对象在 3–8 个维度上的综合对比", z.object({
  title: str,
  axes: z.array(str),
  series: z.array(Series.ref),
  opts: opts({ max: num, decimals }),
}), { group: "高级图表", needs: ["Series"], example: 'radar = RadarChart("能力对比", ["推理", "代码", "长文"], [Series("A", [9, 8, 7]), Series("B", [8, 9, 6])], {max: 10})' });
export const Histogram = def("Histogram", "一组原始数值的分布；组件自己分箱", z.object({
  title: str,
  values: z.array(num),
  opts: opts({ bins: z.number().int().min(2).max(40), unit: str, decimals }),
}), { group: "高级图表", example: 'dist = Histogram("响应时间分布", times, {unit: "ms"})' });
export const BoxPlot = def("BoxPlot", "几组原始数值的分布对比（中位数、四分位、离群值）；组件自己算统计量", z.object({
  title: str,
  groups: z.array(Series.ref),
  opts: opts({ unit: str, decimals }),
}), { group: "高级图表", needs: ["Series"], example: 'box = BoxPlot("各地区客单价", [Series("华东", east), Series("华北", north)], {unit: "元"})' });
export const Heatmap = def("Heatmap", "两个维度交叉的数值矩阵（时段×星期、地区×月份）", z.object({
  title: str,
  columns: z.array(str),
  rows: z.array(str),
  values: z.array(z.array(num)),
  opts: opts({ unit: str, decimals, better }),
}), {
  group: "高级图表",
  notes: ["values 每行对应 rows 的一项，每行的数与 columns 一一对应。"],
  example: 'heat = Heatmap("各时段订单", ["周一", "周二"], ["上午", "下午"], [[12, 18], [30, 25]])',
});
export const Waterfall = def("Waterfall", "从起点经过逐项增减到终点的拆解（利润桥、预算变化）；组件累计并算出终值", z.object({
  title: str,
  labels: z.array(str),
  changes: z.array(num),
  opts: opts({ start: num, startLabel: str, endLabel: str, unit: str, decimals }),
}), {
  group: "高级图表",
  notes: ["changes 是带符号的增减量，不写累计值；start 是起点值，终值由组件算出。"],
  example: 'bridge = Waterfall("利润变化", ["销量", "价格", "成本"], [120, -40, -30], {start: 800, startLabel: "去年", endLabel: "今年", unit: "万元"})',
});
export const Funnel = def("Funnel", "逐级转化（访问→注册→付费）；组件算每级转化率和总转化率", z.object({
  title: str,
  stages: z.array(str),
  values: z.array(num),
  opts: opts({ unit: str, decimals }),
}), { group: "高级图表", example: 'f = Funnel("转化", ["访问", "注册", "付费"], [10000, 1800, 240], {unit: "人"})' });
export const Treemap = def("Treemap", "很多部分组成一个整体，按面积比较大小（市值、预算科目）", z.object({
  title: str,
  items: z.array(z.object({ name: str, value: num, group: str.optional() })),
  opts: opts({ unit: str, decimals }),
}), { group: "高级图表", example: 'tm = Treemap("各业务收入", [{name: "云", value: 320, group: "企业"}, {name: "广告", value: 210}], {unit: "亿元"})' });
export const Sankey = def("Sankey", "数量从来源流向去处（预算分配、流量路径、能量流）", z.object({
  title: str,
  links: z.array(z.object({ from: str, to: str, value: num })),
  opts: opts({ unit: str, decimals }),
}), { group: "高级图表", example: 's = Sankey("流量去向", [{from: "搜索", to: "注册", value: 300}, {from: "搜索", to: "离开", value: 700}])' });
export const Candlestick = def("Candlestick", "价格的开高低收（K 线）；默认红涨绿跌", z.object({
  title: str,
  dates: z.array(str),
  prices: z.array(z.array(num)),
  opts: opts({ unit: str, decimals, rise: z.enum(["red", "green"]) }),
}), {
  group: "高级图表",
  notes: ["prices 每项是 [开, 高, 低, 收]。"],
  example: 'k = Candlestick("日 K", ["10-28", "10-29"], [[10.2, 10.8, 10.1, 10.6], [10.6, 10.7, 10.0, 10.1]], {unit: "元"})',
});
export const Dumbbell = def("Dumbbell", "每个类别的两个值及其差距：前后、预期与实际、去年与今年；组件算差值", z.object({
  title: str,
  categories: z.array(str),
  from: Series.ref,
  to: Series.ref,
  opts: opts({ unit: str, decimals, better }),
}), {
  group: "高级图表",
  needs: ["Series"],
  example: 'gap = Dumbbell("预期与实际", items.name, Series("预期", items.expected), Series("实际", items.actual), {unit: "亿元"})',
});

// ---------- 过程 ----------
const status = z.enum(["done", "current", "todo", "error"]);
export const Timeline = def("Timeline", "按时间排列的事件、里程碑或版本历史", z.object({
  events: z.array(z.object({ date: str, title: str, text: str.optional(), tone: tone.optional() })),
  opts: opts({ order: z.enum(["asc", "desc"]) }),
}), { group: "过程", example: 'events = Timeline([{date: "2026-09", title: "发布", text: "首个公开版本"}, {date: "2026-10", title: "降价", tone: "success"}])' });
export const Steps = def("Steps", "有先后顺序的步骤或流程状态；current 为当前步骤的序号（从 0 开始）", z.object({
  steps: z.array(z.object({ title: str, text: str.optional(), status: status.optional() })),
  opts: opts({ current: z.number().int().min(0), direction: z.enum(["vertical", "horizontal"]) }),
}), { group: "过程", example: 'how = Steps([{title: "申请", text: "提交材料"}, {title: "审核"}, {title: "放款"}], {current: 1})' });
export const Checklist = def("Checklist", "逐项检查是否满足；组件统计完成数", z.object({
  items: z.array(z.object({ text: str, done: z.boolean(), note: str.optional() })),
  opts: opts({ title: str }),
}), { group: "过程", example: 'ready = Checklist([{text: "备份完成", done: true}, {text: "灰度验证", done: false}], {title: "上线检查"})' });
export const Gantt = def("Gantt", "任务的起止时间与重叠（项目排期、路线图）", z.object({
  title: str,
  tasks: z.array(z.object({ name: str, start: str, end: str, group: str.optional(), done: z.boolean().optional() })),
}), {
  group: "过程",
  notes: ["start/end 写成 YYYY-MM-DD；同一 group 的任务同色。"],
  example: 'plan = Gantt("排期", [{name: "设计", start: "2026-11-01", end: "2026-11-10"}, {name: "开发", start: "2026-11-08", end: "2026-12-05"}])',
});

// ---------- 对比 ----------
export const Table = def("Table", `逐项核对的表格；rows 为二维数组，最多 ${MAX_TABLE_ROWS} 行，读者可点表头排序`, z.object({
  columns: z.array(str),
  rows: z.array(z.array(z.union([str, num, z.null()]))),
  opts: opts({ decimals }),
}), {
  group: "对比",
  notes: ["每行的单元格数与列数相同、顺序一致，通常写成 @Each(items, \"x\", [x.name, x.amount])；合计不放进表格。"],
  example: 'detail = Table(["门店", "金额"], @Each(items, "x", [x.name, x.amount]), {decimals: 2})',
});
export const Compare = def("Compare", "几个选项在多个属性上的对比矩阵；better 指出每行哪边好，组件标出每行最优", z.object({
  options: z.array(str),
  rows: z.array(z.object({ label: str, values: z.array(scalar), better: better.optional(), unit: str.optional() })),
  opts: opts({ decimals, recommend: str }),
}), {
  group: "对比",
  notes: ["values 与 options 一一对应；true/false 显示为支持/不支持。recommend 填推荐的选项名（必须在 options 中）。比较两三个产品、方案、型号时优先用它。"],
  example: 'cmp = Compare(["A", "B"], [{label: "价格", values: [20, 25], better: "low", unit: "美元/月"}, {label: "本地部署", values: [true, false]}], {recommend: "A"})',
});
export const Ranking = def("Ranking", "按数值排名的列表；组件排序、编号并画出比例条", z.object({
  title: str,
  items: z.array(z.object({ name: str, value: num, note: str.optional() })),
  opts: opts({ unit: str, decimals, better, top: z.number().int().min(1).max(50) }),
}), {
  group: "对比",
  notes: ["items 不用事先排序；better 为 low 时数值小的排前面。top 只显示前几名。"],
  example: 'rank = Ranking("门店销售额", @Each(stores, "s", {name: s.name, value: s.amount}), {unit: "万元", top: 5})',
});
export const ProsCons = def("ProsCons", "一个选择的优点与缺点", z.object({
  pros: z.array(str),
  cons: z.array(str),
  opts: opts({ title: str, prosLabel: str, consLabel: str }),
}), { group: "对比", example: 'pc = ProsCons(["便宜", "开源"], ["文档少"], {title: "方案 A"})' });
export const EntityCard = def("EntityCard", "一个具体对象的名片：产品、公司、人物、地点、方案或套餐", z.object({
  name: str,
  opts: opts({
    subtitle: str,
    description: str,
    fields: z.array(z.object({ label: str, value: scalar, unit: str.optional() })),
    tags: z.array(str),
    badge: str,
    rating: num,
    price: num,
    priceUnit: str,
    highlight: z.boolean(),
    decimals,
  }),
}), {
  group: "对比",
  notes: ["几个 EntityCard 放进 Grid 做套餐或候选对比；highlight 为 true 的一张突出显示。rating 为 0–5 分。"],
  example: 'pro = EntityCard("专业版", {price: 99, priceUnit: "元/人/月", fields: [{label: "席位上限", value: 200}], tags: ["SSO"], highlight: true})',
});

// ---------- 图示与代码 ----------
export const Flow = def("Flow", "流程图或架构图：节点和有向连线，组件自动分层布局", z.object({
  nodes: z.array(z.object({ id: str, label: str, tone: tone.optional() })),
  edges: z.array(z.object({ from: str, to: str, label: str.optional() })),
  opts: opts({ direction: z.enum(["right", "down"]), title: str }),
}), {
  group: "图示与代码",
  notes: ["id 用英文，edges 的 from/to 写 id；最多约 30 个节点。有判断分支时在连线上写 label。"],
  example: 'arch = Flow([{id: "u", label: "用户"}, {id: "api", label: "API"}, {id: "db", label: "数据库"}], [{from: "u", to: "api"}, {from: "api", to: "db", label: "SQL"}])',
});
export const Tree = def("Tree", "层级结构：组织架构、分类体系、知识脉络、思维导图", z.object({
  root: z.object({ name: str, note: str.optional(), children: z.array(z.any()).optional() }),
  opts: opts({ title: str, depth: z.number().int().min(1).max(6) }),
}), {
  group: "图示与代码",
  notes: ["每个节点是 {name, note?, children?}，children 是同样结构的数组；depth 为默认展开的层数。"],
  example: 'org = Tree({name: "公司", children: [{name: "研发", children: [{name: "前端"}, {name: "后端"}]}, {name: "销售"}]})',
});
export const Diff = def("Diff", "修改前后的文本或代码，组件按行标出增删", z.object({
  before: str,
  after: str,
  opts: opts({ language: str, title: str }),
}), { group: "图示与代码", example: 'd = Diff("timeout = 30", "timeout = 60", {language: "python"})' });
export const FileTree = def("FileTree", "目录结构；路径用 / 分隔，目录以 / 结尾", z.object({
  paths: z.array(str),
  opts: opts({ title: str }),
}), { group: "图示与代码", example: 'files = FileTree(["src/", "src/app.ts", "src/lib/db.ts", "README.md"])' });
export const JsonView = def("JsonView", "可折叠的 JSON 数据（接口返回、配置）", z.object({
  data: z.any(),
  opts: opts({ title: str, depth: z.number().int().min(1).max(6) }),
}), { group: "图示与代码", example: 'resp = JsonView({id: 7, tags: ["a", "b"], owner: {name: "lin"}}, {title: "返回值"})' });

// ---------- 交互 ----------
export const Select = def("Select", "从几个选项中选一个（multiple 为 true 时可多选，值为数组）；选项少而短时显示为分段按钮", z.object({
  label: str,
  options: z.array(str),
  value: bound(z.union([str, z.array(str)]).optional()),
  opts: opts({ multiple: z.boolean() }),
}), { group: "交互" });
export const Slider = def("Slider", "在范围内调一个数值，例如预算、数量、比例", z.object({
  label: str,
  min: num,
  max: num,
  value: bound(num.optional()),
  opts: opts({ step: num, unit: str }),
}), { group: "交互" });
export const Toggle = def("Toggle", "开或关的一个选项", z.object({ label: str, value: bound(z.boolean().optional()) }), { group: "交互" });
export const Input = def("Input", "让用户填一小段文字或数字，通常再由 Button 把它发出去", z.object({
  label: str,
  value: bound(z.union([str, num]).optional()),
  opts: opts({ placeholder: str, type: z.enum(["text", "number", "date"]) }),
}), { group: "交互" });
export const Button = def("Button", "帮助用户进行下一步的按钮，点击后以用户身份发送 @ToAssistant 中的文字", z.object({
  label: str,
  action: z.union([steps.ToAssistant.ref]).optional(),
  opts: opts({ tone: z.enum(["default", "primary"]) }),
}), { group: "交互" });

// ---------- 页面 ----------
export const HtmlArtifact = def("HtmlArtifact", "一个完整的 HTML 页面（含 CSS、JavaScript），在隔离的框架中运行；fallbackText 是页面要点的纯文字版", z.object({
  title: str,
  document: str.max(MAX_HTML_CHARS),
  fallbackText: str.max(MAX_FALLBACK_CHARS),
}), {
  group: "页面",
  notes: [
    "只用于组件无法表达的内容：交互模拟、动画演示、自定义图示、小工具或小游戏；静态的数值对照、时间轴、流程和对比都有组件。",
    "写在 openui 围栏里：`root = Stack([page])`，再写 `page = HtmlArtifact(\"标题\", '<!doctype html>…完整页面…', \"页面要点的纯文字版\")`；不用 html 代码围栏输出。",
    "document 用单引号字符串，内容按页面源码原样写，可以直接换行，JavaScript 里的反斜杠（如 \\\"、\\n）照常只写一个；页面里的 HTML 属性和 JavaScript 字符串用双引号，单引号要写成 \\'。",
    "页面在隔离的框架里运行，无法联网：不引用外部脚本、样式、字体或图片，不用 fetch、localStorage、alert 和表单提交；图形用内联 SVG 或 canvas。",
    `document 不超过 ${MAX_HTML_CHARS} 个字符；fallbackText 不超过 ${MAX_FALLBACK_CHARS} 个字符，写清页面展示的结论和关键数据，供复制、搜索和打印使用。`,
    "配色用宿主变量 var(--fg)、var(--muted)、var(--bg)、var(--surface)、var(--border)、var(--accent) 和 var(--series-1) 到 var(--series-6)，背景保持透明，深色浅色自动一致；宽度自适应（手机约 320px），高度随内容，不用 100vh 或固定高度。",
    "页面不能替用户发消息；需要用户下一步时，在 HtmlArtifact 之外放 Button。",
  ],
});

export const DEFINITIONS = [
  Stack, Grid, Card, Tabs, Tab, Details, Divider,
  Heading, Text, Callout, Badge, Quote, List, KeyValue, Code,
  Metric, Progress, Gauge, ShareBar, Highlights,
  LineChart, AreaChart, BarChart, ComboChart, PieChart, Series,
  ScatterChart, RadarChart, Histogram, BoxPlot, Heatmap, Waterfall, Funnel, Treemap, Sankey, Candlestick, Dumbbell,
  Timeline, Steps, Checklist, Gantt,
  Table, Compare, Ranking, ProsCons, EntityCard,
  Flow, Tree, Diff, FileTree, JsonView,
  Select, Slider, Toggle, Input, Button,
  HtmlArtifact,
];
export const DEFINED_NAMES = DEFINITIONS.map((d) => d.name);
const SUBCOMPONENTS = new Set(["Series", "Tab"]);

Content = z.union(DEFINITIONS.filter((d) => !SUBCOMPONENTS.has(d.name)).map((d) => d.ref) as unknown as [z.ZodType, z.ZodType, ...z.ZodType[]]);

/** The schema-only library: prompt generation, parsing for fallbacks. The React library reuses these definitions. */
export const schemaLibrary = createLibrary({
  components: DEFINITIONS,
  componentGroups: GROUPS.map((name) => ({ name, components: DEFINED_NAMES.filter((n) => META[n].group === name) })),
  root: "Stack",
});
