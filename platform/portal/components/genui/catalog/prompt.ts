// The GenUI contract, generated from the catalog so what the model is told is exactly what the Portal renders. Three
// parts, disclosed progressively (design 2026-10-30-genui-registry-v2 §3):
// - index: in every system prompt; when a UI helps and which component fits what, by name only.
// - guide: the language and the rules; the Agent returns it with the first `ui_components` call of a run.
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
  "几个方案或产品：Compare、EntityCard、ProsCons；逐项核对：Table；一个对象的属性：KeyValue",
  "过程与时间：Timeline、Steps、Checklist、Gantt；流程或架构：Flow；层级：Tree",
  "代码与数据：Diff、FileTree、JsonView、Code",
  "试算、筛选、切换视图：Select、Slider、Toggle、Input 加表达式；帮用户发出下一步请求：Button",
  "组件画不出的交互模拟、动画、小工具或小游戏：HtmlArtifact",
  "布局：Stack、Grid、Card、Tabs、Tab、Details、Divider；文字：Heading、Text、Callout、Badge、Quote、List",
  "图表数据：Series",
];

const INDEX = `## 回答中的界面（GenUI）

回答里可以插入界面块（以 \`\`\`openui 开头、以 \`\`\` 结束的围栏），帮用户看懂趋势、对比、构成、过程，或就地试算。写界面块之前先调用 ui_components，names 填打算使用的组件名，按返回的签名和示例写；没有读过说明的组件不要写。

不用界面：简单事实、概念解释、翻译、改写、闲聊、单个数值，或用户要求纯文字时。数据不足时说明缺口，不凑图。

按要表达的内容选组件：
${PICK.map((line) => `- ${line}`).join("\n")}

一个回答可以有多个界面块，与文字穿插：回答分几部分讲（例如先看趋势、再比方案、最后试算）时，每部分在对应文字旁放自己的块；只有一个要点时用一个块。块之间不共享数据和变量，需要联动的控件和结果放在同一个块里。不要用图、表和 Metric 重复同一组数。

有界面块时，数字和结论由界面给出：最高最低、合计、变化、名次、占比、谁更优，交给组件（Highlights、Ranking、Compare、Metric 的 previous 等）或表达式从原始数据算出。界面块外只写一两句解读、原因或建议，不写数字、名次或比较结论。用户没有要求时不做回归、显著性检验、置信区间、预测或加权评分。
`;

const EXAMPLE = `## 写法

界面块是一段 openui-lang 程序，每个围栏独立，必须有自己的 root；围栏外的文字照常按 Markdown 显示。示例（数值仅为示意，实际只能用上下文中的数据）：

\`\`\`openui
root = Stack([chart, facts])
months = ["1月", "2月", "3月", "4月"]
sales = [120, 135, 128, 160]
chart = LineChart("月销售额", months, [Series("销售额", sales)], {unit: "万元", marks: ["max"]})
facts = Highlights(months, sales, ["change", "rise"], {unit: "万元"})
\`\`\`

位置参数只放主要数据，其余可选项都写进最后一个 opts 对象，例如 \`BarChart("销量", months, [Series("线上", a)], {stacked: true, unit: "万元"})\`；opts 的键各组件同义：unit 单位、decimals 小数位（0–4）、tone 语气色、better（high 越大越好，low 越小越好）。不要把 opts 的键写成单独的位置参数，也不要写成 \`key: value\` 形式的命名参数。`;

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

切换视图用三元表达式，例如 \`view = $show == "表格" ? table : chart\`。Button 的第二个参数只能是 @ToAssistant("要发送的话")，可以拼接变量；点击后这句话以用户身份发出。不写 Action([...])，不用其它步骤。`;

const RULES = [
  "根据用户要理解或决定的问题，主动选择最容易读懂的表达方式；不要求用户先提出“使用 UI”。用户明确要求文字、表格或某种图表时，优先遵循。",
  "用户要求可视化、画图或图表时，至少给一个图表组件，只给表格不算可视化；单位不同的指标不放进同一个图（ComboChart 的柱和线除外）。",
  "只用上下文中已有的数据，不得编造；保留单位、时间范围和必要的不确定性说明。",
  "原数据只写一次，多个对象的数据写成对象数组，例如 `items = [{name: \"M6\", amount: 420}, {name: \"M8\", amount: 442}]`，表格、图表和文字都从它取值（`items.amount` 取出一列）。",
  "派生数交给组件算，不要自己算：变化率用 Metric 的 previous，占比用 PieChart 或 ShareBar，排名用 Ranking，每项最优用 Compare 的 better，最高、最低、合计、平均、首尾变化、最大增幅用 Highlights，累计和终值用 Waterfall，转化率用 Funnel，差距用 Dumbbell。组件没有的派生数用表达式（`price = 80 * 0.9`），不写手算的结果。",
  "Text 里需要名称或数字时用表达式拼，例如 `top = @First(@Sort(items, \"amount\", \"desc\"))`、`Text(top.name + \" 最高\")`。比较两个值的高低多少用 Metric 的 previous、Compare 或 Dumbbell，不用三元表达式拼“多/少”“高/低”。",
  "换算单位或比例时从原数据用表达式算，例如 `wan = @Each(income, \"v\", v / 10000)`，不写换算后的数。",
  "Table 的 rows 每行一个数组，单元格数与列数相同、顺序一致；`+` 只用于数字和字符串，不用来连接数组；合计不放进表格。列名、行标签和对象的称呼逐字沿用上下文。",
  "字符串里拼接的算式整体加括号：`Text(\"增加 \" + (b.w - a.w) + \" 万元\")`。`items[1]` 取出的就是对象，直接读 `items[1].w`；@First 和 @Last 只用于数组。",
  "同一组数据只呈现一次：不用图和表重复同一组数，不再附同样数据的 Markdown 表格。",
  "语句名、对象的字段名和 `$变量` 名只用英文字母、数字和下划线，例如 `chart`、`items`、`$seats`；中文只写在字符串里。",
  "让布局表达层次，省去多余的二级标签和说明；必要的单位、图例和数据限制不能省略。不为装饰增加卡片、Tabs 或按钮；用户没有要后续操作时不放按钮。",
  `每个回答最多 ${MAX_BLOCKS} 个界面块，每块不超过 60 条语句、${MAX_BLOCK_CHARS} 个字符。界面块里不写 URL；组件的文字参数里不写 HTML、CSS 或 JavaScript。`,
  "在 Work 模式中，界面块只放在最终回答里，不放在工具调用之前的过程说明中。",
  "用户要求修改上一轮的界面时，先用 ui_components 读取要用的组件，再输出修改后的完整界面块（包含 root），不要只输出变化的语句；改完核对列、字段和单位仍然一一对应。目录做不到的要求直接说明，不声称已经做到。",
];

const SYNTAX = `## 语法

- 每行一条语句 \`name = 表达式\`。第一行写 \`root = 组件\`，多个部件用 \`Stack([...])\` 包起来，界面先出骨架；名字可以先引用、后定义，数据放在最后。
- 表达式：字符串 "…"、数字、true/false、null、数组 [...]、对象 {key: 值}、组件调用 Name(参数…)。参数按位置传，末尾的可选参数可以省略。
- 除 root 外，每个定义的名字都必须被引用到，否则不会显示。
- \`$name = 默认值\` 声明可变状态，交给控件的 value，其它表达式读取它。
- 运算：+ - * / %（有一侧是字符串时 + 是拼接）、== != > < >= <=、&& || !、三元 \`条件 ? 甲 : 乙\`、括号。
- \`items.field\` 从对象数组取出一列；\`items[0]\` 取一个元素。
- 字符串用双引号，内部的双引号写成 \\"；文字里引用词语用中文引号“”。`;

/** The built-in @functions, one line each, as the library documents them (they match its evaluator). */
function builtins() {
  const section = schemaLibrary.prompt({ toolCalls: false, bindings: true, builtinFunctions: true }).split(/\n(?=## )/).find((s) => s.startsWith("## Built-in Functions"))!;
  const lines = section.split("\n").filter((line) => line.startsWith("@"));
  return ["## 内置函数（只有这些）", "", ...lines, "", "@Each 的模板直接写在调用里，例如 `@Each(rows, \"r\", [r.name, r.amount])`；循环变量只在模板内有效，不要把模板拆成单独的语句。"].join("\n");
}

function generic() {
  return schemaLibrary.prompt({ toolCalls: false, bindings: true, builtinFunctions: true }).split(/\n(?=## )/);
}

export function buildIndex(): string {
  return INDEX;
}

export function buildGuide(): string {
  return [EXAMPLE, SYNTAX, builtins(), ACTION, `## 规则\n\n${RULES.map((rule) => `- ${rule}`).join("\n")}`].join("\n\n") + "\n";
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
    groups: GROUPS.map((name) => ({ name, components: DEFINED_NAMES.filter((n) => META[n].group === name) })),
    components: buildDocs(),
  };
}
