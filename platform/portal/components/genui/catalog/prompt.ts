// The GenUI contract, generated from the catalog so what the model is told is exactly what the Portal renders. Three
// parts, disclosed progressively (design 2026-10-30-genui-registry-v2 §3):
// - index: in every system prompt; when a UI helps and which component fits what, by name only.
// - guide: the language and the rules; the Agent returns it when the complete guide is absent from the retained context.
// - docs: one per component, signature, notes and an example; returned for the names asked for.
// The output is committed under platform/ailab_platform/contracts/genui/ and a test regenerates it byte for byte.
// Every line of the index and guide that names HtmlArtifact stands alone: the Agent drops such lines when generated
// pages are off, and HtmlArtifact's own rules are in its doc, which is then not offered.
import { CATALOG_VERSION, DEFINED_NAMES, FORMAT, GROUPS, MAX_BLOCK_CHARS, MAX_BLOCKS, META, schemaLibrary } from "./schema.ts";

/** Which component for which intent; the test checks every component is named here. */
const PICK = [
  "趋势：LineChart、AreaChart；数量和比率放同一张图：ComboChart",
  "类别对比：BarChart；排名：Ranking；前后、预期与实际：Dumbbell；正负偏差：BarChart（带符号的数）",
  "构成：PieChart、ShareBar、Treemap；流向：Sankey；增减拆解：Waterfall；逐级转化：Funnel",
  "关系与分布：ScatterChart、Histogram、BoxPlot、Heatmap；多维评分：RadarChart；K 线：Candlestick",
  "结论数字（最高、最低、合计、首尾变化、最大增幅）：Highlights；关键指标：Metric、Progress、Gauge",
  "几个方案或产品：Compare、EntityCard、ProsCons；分档、分类、分层：Segments；逐项核对：Table；一个对象的属性：KeyValue",
  "过程与时间：Timeline、Steps、Checklist、Gantt；流程、因果链或架构：Flow；层级：Tree",
  "代码与数据：Diff、FileTree、JsonView、Code；网页图片：Image、Gallery、ImageText",
  "试算、筛选、切换视图：Select、Slider、Toggle、Input 加表达式；帮用户发出下一步请求：Button",
  "组件画不出的交互模拟、动画、小工具或小游戏：HtmlArtifact",
  "布局：Stack、Grid、Card、Tabs、Tab、Details、Divider；文字：Heading、Text、Callout、Badge、Quote、List",
  "图表数据：Series",
];

/**
 * Components whose docs ride in the system prompt, so the everyday answer (a comparison, a set of tiers, steps, a
 * few numbers, a chart) needs no tool round trip. The rest stay one ui_components call away. Choose by how often an
 * answer that is not explicitly about visualisation would want them; the token cost is checked in tests.
 */
// Highlights is here because the rules route every max/min/total/change fact to it; written from memory it broke.
// Segments and Steps left on 2026-11-13: 2 of 68 answers with a UI block used either (prompt-diet design).
export const CORE = ["Stack", "Grid", "Text", "Callout", "Table", "Compare", "Metric", "Highlights", "LineChart", "BarChart", "Series"];
/** Core components whose example teaches a data shape the signature alone leaves open. */
const CORE_EXAMPLES = new Set(["Table", "Compare"]);

const INDEX = `## 回答中的界面（GenUI）

回答里可插入界面块（\`\`\`openui 围栏）代替成段文字。按内容的形状决定，不按话题，也不等用户要求：
- 三个以上的数，要看趋势、对比、构成或排名；
- 几个对象、档位或方案，在相同几个方面比较；
- 步骤、流程、时间线、计划或因果链；
- 选择或试算（价格、预算、配置）。
讲解中出现这些结构时也用界面承载那一部分，文字只做解读，这不算扩展回答。
不用界面：一两句能答完的事实或数值、翻译、改写、成稿写作、代码讲解、闲聊，或用户要纯文字；不为讲解自造数据，数据不足就说明缺口。

选组件：
${PICK.map((line) => `- ${line}`).join("\n")}

下方常用组件可直接写；其他组件先调用 ui_components（names 填组件名）读说明，没读过说明的不写。
`;

const SYNTAX = `## 写法

每个围栏是一段独立的 openui-lang 程序，围栏外照常写 Markdown。例（数值仅示意）：

\`\`\`openui
root = Stack([chart, facts])
items = [{m: "1月", v: 120}, {m: "2月", v: 135}, {m: "3月", v: 128}, {m: "4月", v: 160}]
chart = LineChart("月销售额", items.m, [Series("销售额", items.v)], {unit: "万元", marks: ["max"]})
facts = Metric("4月", @Last(items.v), {unit: "万元", previous: items[2].v})
\`\`\`

- 每行一条 \`name = 表达式\`，第一行 \`root = Stack([...])\`；名字可先引用后定义，数据放最后；除 root 外每个名字都要在同一块里定义并被引用，否则不显示。
- 参数按位置传，末尾可选参数可省略；其余可选项放进最后的 opts 对象（如 \`{stacked: true, unit: "万元"}\`），不用 \`key: value\` 命名参数。通用 opts：unit 单位、decimals 小数位（0–4）、tone（neutral、success、warning、danger、info）、better（high 越大越好，low 越小越好）。
- 表达式：双引号字符串（内部引号写 \\"，引用词语用“”）、数字、true/false、null、数组、对象；+ - * / %（字符串相加即拼接）、== != > < >= <=、&& || !、\`条件 ? 甲 : 乙\`；\`items.field\` 取一列，\`items[0]\` 取一项。名字、字段和 \`$变量\` 只用英文字母、数字和下划线，中文只写在字符串里。
- ${builtins()}`;

const RULES = [
  "只用上下文已有的数据，不编造；保留单位、时间范围和不确定性。原数据只写一次（多个对象写成对象数组），图、表和文字都从它取值。",
  "派生数交给组件或表达式算，不写手算结果：变化率用 Metric 的 previous，占比 PieChart/ShareBar，排名 Ranking，逐项最优 Compare 的 better，最高最低、合计平均、首尾变化、最大增幅 Highlights，累计 Waterfall，转化率 Funnel，差距 Dumbbell；其余用表达式（`wan = @Each(income, \"v\", v / 10000)`）。",
  "Text 里的名称和数字用表达式拼，算式整体加括号，如 `Text(top.m + \" 多 \" + (b.v - a.v) + \" 万元\")`；不用三元表达式拼“多/少”。",
  "有界面块时数字和结论由界面给出，块外只写一两句解读、原因或建议，不复述数字和比较结论；同一组数只呈现一次，不用图、表、Metric 或 Markdown 表格重复。用户没要求时不做回归、显著性检验、预测或加权评分。",
  "用户要可视化时至少给一个图表组件，指定了文字、表格或某种图就照做；单位不同的指标不放同一个图（ComboChart 的柱和线除外）；列名、标签和称呼逐字沿用上下文。",
  "用布局表达层次，不加多余的二级标签和说明，不为装饰加卡片或 Tabs；用户没要后续操作时不放按钮。",
  `回答分几部分时每部分配自己的块；块间不共享变量，联动的控件和结果放同一块。每个回答最多 ${MAX_BLOCKS} 块，每块不超过 60 条语句、${MAX_BLOCK_CHARS} 字符；块里不写 URL、HTML、CSS 或 JavaScript。`,
  "Work 模式只在最终回答里放界面块。修改上一轮的界面时输出含 root 的完整新块，并核对列、字段和单位仍对应；做不到的直接说明。",
];

const ACTION = `## 交互与按钮

Select、Slider、Toggle、Input 的 value 写成 \`$变量\`，用户在页面上改动它；其它组件的参数可以用表达式读取这个变量，界面立即重新计算，不需要再问你。先用 \`$变量 = 默认值\` 声明默认值。例如（价格仅为示意）：

\`\`\`openui
root = Stack([inputs, total, ask])
$tier = "标准版"
$seats = 20
inputs = Grid([Select("版本", ["标准版", "专业版"], $tier), Slider("人数", 5, 200, $seats, {step: 5, unit: "人"})])
total = Metric("每月费用", $seats * ($tier == "专业版" ? 99 : 49), {unit: "元"})
ask = Button("按这个方案出报价单", @ToAssistant("按" + $tier + "、" + $seats + " 人出一份报价单"))
\`\`\`

切换视图用三元表达式，例如 \`view = $show == "表格" ? table : chart\`。Button 的第二个参数只能是 @ToAssistant("要发送的话")，可以拼接变量；点击后这句话以用户身份发出。不写 Action([...])，不用其它步骤。
`;

/** The built-in @functions, compact, as the library declares them (they match its evaluator). */
function builtins() {
  const section = schemaLibrary.prompt({ toolCalls: false, bindings: true, builtinFunctions: true }).split(/\n(?=## )/).find((s) => s.startsWith("## Built-in Functions"))!;
  const calls = section.split("\n").filter((line) => line.startsWith("@")).map((line) => line.split(" →")[0].split(" — ")[0].trim().replace(/: (any|number|string)(\[\])?(?=[,)])/g, ""));
  // one-argument functions read fine by name: what they take is in the name (@Sum of numbers, @Abs of a number)
  const single = calls.filter((c) => !c.includes(",")).map((c) => c.split("(")[0]);
  const multi = calls.filter((c) => c.includes(","));
  return `内置函数只有：${single.join(" ")}（一个数组或数）；${multi.join("、")}。@Each 的模板直接写在调用里，如 \`@Each(rows, "r", [r.name, r.amount])\`，循环变量只在模板内有效。`;
}

function generic() {
  return schemaLibrary.prompt({ toolCalls: false, bindings: true, builtinFunctions: true }).split(/\n(?=## )/);
}

/** A core component as the system prompt shows it: signature and notes, the example only where it teaches a shape. */
function coreDoc(doc: { name: string; doc: string }) {
  return doc.doc.split("\n").filter((line) => CORE_EXAMPLES.has(doc.name) || !line.startsWith("例：")).join("\n");
}

export function buildIndex(): string {
  const docs = buildDocs().filter((d) => CORE.includes(d.name));
  return [INDEX.trimEnd(), SYNTAX, `## 规则\n\n${RULES.map((rule) => `- ${rule}`).join("\n")}`, `## 常用组件\n\n${docs.map(coreDoc).join("\n")}`].join("\n\n") + "\n";
}

/** What the first ui_components call adds: everything the index leaves out is in the per-component docs. */
export function buildGuide(): string {
  return ACTION;
}

/** Each component's signature line as the library prints it (it matches the parser), with our notes and example. */
export function buildDocs(): { name: string; group: string; needs: string[]; doc: string }[] {
  const signatures = new Map(
    generic()
      .find((s) => s.startsWith("## Component Signatures"))!
      .split("\n")
      .map((line) => [/^(\w+)\(/.exec(line)?.[1], line] as const)
      .filter((entry): entry is [string, string] => !!entry[0]),
  );
  return DEFINED_NAMES.map((name) => {
    const meta = META[name];
    const signature = signatures.get(name);
    if (!signature) throw new Error(`no signature for ${name}`);
    const doc = [signature, ...(meta.notes ?? []).map((n) => `- ${n}`), meta.example ? `例：\`${meta.example}\`` : ""].filter(Boolean).join("\n");
    return { name, group: meta.group, needs: meta.needs ?? [], doc };
  });
}

export function buildCatalog(indexSha256: string, guideSha256: string) {
  return {
    format: FORMAT,
    version: CATALOG_VERSION,
    index_sha256: indexSha256,
    guide_sha256: guideSha256,
    core: CORE,
    groups: GROUPS.map((name) => ({ name, components: DEFINED_NAMES.filter((n) => META[n].group === name) })),
    components: buildDocs(),
  };
}
