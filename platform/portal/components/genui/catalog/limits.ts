// What the chat needs to know about the catalog before any block appears: plain values, no zod or OpenUI, so the
// chat page does not load the renderer's dependencies until the first UI block (HLD §6.10).
export const FORMAT = "openui";
export const CATALOG_VERSION = 2;
export const MAX_BLOCKS = 8;
export const MAX_BLOCK_CHARS = 32000; // = AILAB_GENUI_MAX_BLOCK_CHARS default
export const MAX_HTML_CHARS = 24000; // an HtmlArtifact document, inside the block limit
export const MAX_FALLBACK_CHARS = 2000;
export const MAX_TABLE_ROWS = 200;
export const MAX_CHART_POINTS = 500;
// the catalog's components in definition order; a test keeps it equal to schema.ts
export const COMPONENT_NAMES = ["Stack", "Grid", "Card", "Tabs", "Tab", "Details", "Divider", "Heading", "Text", "Callout", "Badge", "Quote", "List", "KeyValue", "Code", "Metric", "Progress", "Gauge", "ShareBar", "Highlights", "LineChart", "AreaChart", "BarChart", "ComboChart", "PieChart", "Series", "ScatterChart", "RadarChart", "Histogram", "BoxPlot", "Heatmap", "Waterfall", "Funnel", "Treemap", "Sankey", "Candlestick", "Dumbbell", "Timeline", "Steps", "Checklist", "Gantt", "Table", "Compare", "Ranking", "ProsCons", "EntityCard", "Flow", "Tree", "Diff", "FileTree", "JsonView", "Select", "Slider", "Toggle", "Input", "Button", "HtmlArtifact"];
