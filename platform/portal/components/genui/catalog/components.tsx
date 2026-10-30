"use client";

// React implementations of the catalog. They take only the catalog's props: no class, style or URL from the model;
// colours, type and spacing come from the host tokens (HLD §4.6.5). Controls write `$variables` through OpenUI's
// state store, so every expression that reads them re-evaluates in place. Less common charts and the diagrams load
// in their own chunks the first time a block uses one.
import { Component, lazy, Suspense, useId, useMemo, useState } from "react";
import { createLibrary, defineComponent, useIsStreaming, useStateField, useTriggerAction, type ComponentRenderer } from "@openuidev/react-lang";
import { cn } from "../../../lib/cn";
import { IconChevronDown } from "../../icons";
import { useGenUIHost } from "../host";
import {
  ChecklistView, CodeView, CompareView, DetailsView, EntityCardView, GaugeView, HighlightsView, KeyValueView, ListView, MetricView, ProgressView, ProsConsView,
  QuoteView, RankingView, ShareBarView, StepsView, TimelineView, tone,
} from "./blocks";
import { CartesianChart, HBarChart, PieChartView } from "./charts";
import { formatNumber, pieData, seriesOf, shapeOk, text } from "./data";
import { HtmlArtifactView } from "./html-artifact";
import { Malformed } from "./kit";
import { DEFINITIONS, MAX_TABLE_ROWS, tableRows } from "./schema";

type P = Record<string, any>;
type Render = (v: unknown) => React.ReactNode;

/** One component that failed to render shows a note in its place; the rest of the block is unaffected. A new set of
 * props (the block is still being written, the reader changed a control) tries again. */
class Guard extends Component<{ props: unknown; children: React.ReactNode }, { failed: boolean; props: unknown }> {
  state = { failed: false, props: this.props.props };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  static getDerivedStateFromProps(next: { props: unknown }, state: { failed: boolean; props: unknown }) {
    return next.props !== state.props ? { failed: false, props: next.props } : null;
  }
  render() {
    return this.state.failed ? <Malformed /> : this.props.children;
  }
}

const r = (fn: (props: P, render: Render, id?: string) => React.ReactNode): ComponentRenderer<P> =>
  function Rendered({ props, renderNode, statementId }) {
    return <Guard props={props}>{fn(props, renderNode, statementId)}</Guard>;
  };
const opts = (p: P): P => (p.opts && typeof p.opts === "object" ? p.opts : {});

/** The state key of a component that is not bound to a `$variable`: its statement name, else what it shows. Saved
 * with the answer (lib/ui-state), so it must stay the same across reloads of the same answer. */
const stateKey = (kind: string, id: string | undefined, fallback: string) => `${kind}:${id ?? fallback}`.slice(0, 120);

const MoreChart = lazy(() => import("./charts-more"));
const Diagram = lazy(() => import("./diagrams"));
const loading = <div className="genui-skeleton" role="status" aria-label="加载中" />;
const more = (name: string): ComponentRenderer<P> => r((p, _, id) => <Suspense fallback={loading}><MoreChart name={name} p={p} sid={id} /></Suspense>);
const diagram = (name: string): ComponentRenderer<P> => r((p, _, id) => <Suspense fallback={loading}><Diagram name={name} p={p} sid={id} /></Suspense>);
/** Loads the lazy chunks ahead of time (print waits for them so every block is on paper). */
export const preloadParts = () => Promise.all([import("./charts-more"), import("./diagrams")]);

function SeriesChart({ p, sid, kind }: { p: P; sid?: string; kind: "line" | "area" | "bar" }) {
  const o = opts(p);
  const series = seriesOf(p.series, p.categories);
  if (!series) return <Malformed />;
  const categories = (p.categories as unknown[]).map(text);
  const common = { sid, title: text(p.title), categories, unit: text(o.unit) || undefined, decimals: o.decimals, reference: typeof o.reference === "number" ? o.reference : undefined, referenceLabel: text(o.referenceLabel) || undefined };
  if (kind === "bar" && o.horizontal === true) return <HBarChart {...common} series={series} stacked={o.stacked === true} percent={o.percent === true} better={o.better} />;
  return (
    <CartesianChart {...common} layers={[{ kind, series }]} stacked={o.stacked === true} percent={o.percent === true} curve={o.curve} better={o.better}
      zero={kind !== "line" || o.zero === true} marks={Array.isArray(o.marks) ? o.marks : undefined} />
  );
}

function ComboView({ p, sid }: { p: P; sid?: string }) {
  const o = opts(p);
  const bars = seriesOf(p.bars, p.categories);
  const lines = seriesOf(p.lines, p.categories);
  if (!bars || !lines) return <Malformed />;
  const rightUnit = text(o.lineUnit) && text(o.lineUnit) !== text(o.unit) ? text(o.lineUnit) : undefined;
  return <CartesianChart sid={sid} title={text(p.title)} categories={(p.categories as unknown[]).map(text)} unit={text(o.unit) || undefined} rightUnit={rightUnit} decimals={o.decimals}
    layers={[{ kind: "bar", series: bars }, { kind: "line", series: lines, right: !!rightUnit }]} />;
}

function TabsView({ tabs, render, sid }: { tabs: P[]; render: Render; sid?: string }) {
  const id = useId();
  const list = tabs.filter((t) => t && t.props);
  const field = useStateField<number>(stateKey("tab", sid, list.map((t) => text(t.props.label)).join("|")));
  const setAt = (i: number) => field.setValue(i);
  const current = Math.min(Number.isInteger(field.value) ? Number(field.value) : 0, Math.max(0, list.length - 1));
  return (
    <div className="gu-tabs">
      <div role="tablist" className="gu-tablist" onKeyDown={(e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        const next = (current + (e.key === "ArrowRight" ? 1 : list.length - 1)) % list.length;
        setAt(next);
        (e.currentTarget.children[next] as HTMLElement | undefined)?.focus();
      }}>
        {list.map((t, i) => (
          <button key={i} type="button" role="tab" id={`${id}-t${i}`} aria-controls={`${id}-p${i}`} aria-selected={i === current} tabIndex={i === current ? 0 : -1}
            className="gu-tab" onClick={() => setAt(i)}>
            {text(t.props.label)}
          </button>
        ))}
      </div>
      {list.map((t, i) => (
        // every page prints; only the chosen one shows on screen
        <div key={i} role="tabpanel" id={`${id}-p${i}`} aria-labelledby={`${id}-t${i}`} className={cn("gu-stack", i === current ? "" : "gu-print-only")}>
          <div className="gu-print-label">{text(t.props.label)}</div>
          {render(t.props.children)}
        </div>
      ))}
    </div>
  );
}

function ButtonView({ p }: { p: P }) {
  const trigger = useTriggerAction();
  const streaming = useIsStreaming();
  const host = useGenUIHost();
  const [busy, setBusy] = useState(false);
  const label = text(p.label);
  return (
    <button type="button" className={cn("gu-button", opts(p).tone === "primary" && "primary")} disabled={host.readOnly || streaming || busy} aria-busy={busy || undefined}
      onClick={async () => {
        setBusy(true);
        try {
          await trigger(label, undefined, p.action as any);
        } finally {
          setBusy(false);
        }
      }}>
      {label}
    </button>
  );
}

// Controls: each owns one `$variable`. Without a binding the value lives in the component, still changeable.

function Field({ label, htmlFor, value, children }: { label: string; htmlFor?: string; value?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="gu-field">
      <div className="gu-field-head">
        {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
        {value != null ? <output htmlFor={htmlFor}>{value}</output> : null}
      </div>
      {children}
    </div>
  );
}

function SelectView({ p }: { p: P }) {
  const options: string[] = (Array.isArray(p.options) ? p.options : []).map(text).filter(Boolean);
  const multiple = opts(p).multiple === true;
  const field = useStateField<string | string[]>(`select:${text(p.label)}`, p.value);
  const label = text(p.label);
  const id = useId();
  // a few short options read best side by side; a long list is a menu
  const segmented = options.length <= 4 && options.join("").length <= 18;
  if (multiple) {
    const chosen = new Set((Array.isArray(field.value) ? field.value : typeof field.value === "string" ? [field.value] : []).filter((v) => options.includes(v)));
    return (
      <Field label={label}>
        <div className="gu-chips" role="group" aria-label={label}>
          {options.map((o) => (
            <button key={o} type="button" aria-pressed={chosen.has(o)} onClick={() => {
              const next = new Set(chosen);
              if (next.has(o)) next.delete(o);
              else next.add(o);
              field.setValue(options.filter((x) => next.has(x)));
            }}>{o}</button>
          ))}
        </div>
      </Field>
    );
  }
  const value = typeof field.value === "string" && options.includes(field.value) ? field.value : options[0] ?? "";
  return (
    <Field label={label} htmlFor={segmented ? undefined : id}>
      {segmented ? (
        <div className="gu-segmented" role="radiogroup" aria-label={label}>
          {options.map((o) => (
            <button key={o} type="button" role="radio" aria-checked={o === value} onClick={() => field.setValue(o)}>{o}</button>
          ))}
        </div>
      ) : (
        <select id={id} className="gu-select" value={value} onChange={(e) => field.setValue(e.target.value)}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      )}
    </Field>
  );
}

function SliderView({ p }: { p: P }) {
  const o = opts(p);
  const min = Number.isFinite(Number(p.min)) ? Number(p.min) : 0;
  const max = Number.isFinite(Number(p.max)) && Number(p.max) > min ? Number(p.max) : min + 100;
  const step = Number(o.step) > 0 ? Number(o.step) : (max - min) / 100 >= 1 ? 1 : (max - min) / 100;
  const field = useStateField<number>(`slider:${text(p.label)}`, p.value);
  const raw = Number(field.value);
  const value = Number.isFinite(raw) ? Math.min(max, Math.max(min, raw)) : min;
  const id = useId();
  const unit = text(o.unit);
  return (
    <Field label={text(p.label)} htmlFor={id} value={<>{formatNumber(value)}{unit ? <span className="gu-unit">{unit}</span> : null}</>}>
      <input id={id} type="range" className="gu-range" min={min} max={max} step={step} value={value}
        style={{ "--fill": `${((value - min) / (max - min)) * 100}%` } as React.CSSProperties}
        onChange={(e) => field.setValue(Number(e.target.value))} />
    </Field>
  );
}

function ToggleView({ p }: { p: P }) {
  const field = useStateField<boolean>(`toggle:${text(p.label)}`, p.value);
  const on = field.value === true;
  return (
    <label className="gu-toggle">
      <span>{text(p.label)}</span>
      <button type="button" role="switch" aria-checked={on} className={cn("switch", on && "on")} onClick={() => field.setValue(!on)}>
        <span className="knob" />
      </button>
    </label>
  );
}

function InputView({ p }: { p: P }) {
  const o = opts(p);
  const type = o.type === "number" || o.type === "date" ? o.type : "text";
  const field = useStateField<string | number>(`input:${text(p.label)}`, p.value);
  const id = useId();
  const shown = typeof field.value === "string" || typeof field.value === "number" ? String(field.value) : "";
  return (
    <Field label={text(p.label)} htmlFor={id}>
      <input id={id} type={type} className="gu-input" maxLength={500} placeholder={text(o.placeholder)} value={shown}
        onChange={(e) => field.setValue(type === "number" && e.target.value !== "" && Number.isFinite(Number(e.target.value)) ? Number(e.target.value) : e.target.value)} />
    </Field>
  );
}

type Sort = { col: number; desc: boolean } | null;
const NO_ROWS: unknown[][] = [];

function TableView({ p, sid }: { p: P; sid?: string }) {
  const decimals = opts(p).decimals;
  const columns: string[] = (Array.isArray(p.columns) ? p.columns : []).map(text);
  const valid = tableRows(p.rows, columns.length);
  const rows = valid ?? NO_ROWS;
  // kept as "<column>:asc|desc" so it saves as a plain string
  const field = useStateField<string>(stateKey("sort", sid, columns.join("|")));
  const m = typeof field.value === "string" ? /^(\d+):(asc|desc)$/.exec(field.value) : null;
  const sort: Sort = m && Number(m[1]) < columns.length ? { col: Number(m[1]), desc: m[2] === "desc" } : null;
  const setSort = (next: Sort) => field.setValue(next ? `${next.col}:${next.desc ? "desc" : "asc"}` : "");
  const sorted = useMemo(() => {
    const shown = rows.slice(0, MAX_TABLE_ROWS);
    if (!sort) return shown;
    const key = (v: unknown) => (typeof v === "number" ? v : Number.isFinite(Number(v)) && text(v).trim() !== "" ? Number(v) : text(v));
    return [...shown].sort((a, b) => {
      const x = key(a[sort.col]);
      const y = key(b[sort.col]);
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "zh-CN");
      return sort.desc ? -c : c;
    });
  }, [rows, sort?.col, sort?.desc]);
  const numeric = columns.map((_, ci) => rows.length > 0 && rows.every((row) => row[ci] == null || typeof row[ci] === "number"));
  if (!valid) return <Malformed />;
  return (
    <div className="gu-table-wrap">
      <div className="md-table gu-table">
        <table>
          <thead>
            <tr>{columns.map((c, i) => (
              <th key={i} className={numeric[i] ? "num" : undefined} aria-sort={sort?.col === i ? (sort.desc ? "descending" : "ascending") : undefined}>
                <button type="button" className="gu-sort" onClick={() => setSort(sort?.col === i ? (sort.desc ? null : { col: i, desc: true }) : { col: i, desc: false })}>
                  {c}
                  <span className="gu-sort-mark" data-dir={sort?.col === i ? (sort.desc ? "desc" : "asc") : undefined} aria-hidden="true"><IconChevronDown size={12} /></span>
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {sorted.map((row, ri) => (
              <tr key={ri}>{columns.map((_, ci) => <td key={ci} className={numeric[ci] ? "num" : undefined}>{formatNumber(row[ci], decimals)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > sorted.length ? <div className="gu-caption">共 {rows.length} 行，显示前 {sorted.length} 行</div> : null}
    </div>
  );
}

const IMPL: Record<string, ComponentRenderer<P>> = {
  Stack: r((p, render) => {
    const o = opts(p);
    return <div className={cn("gu-stack", o.direction === "row" && "row")} data-gap={o.gap === "sm" || o.gap === "lg" ? o.gap : "md"} data-align={o.align}>{render(p.children)}</div>;
  }),
  Grid: r((p, render) => <div className="gu-grid" data-cols={Math.min(4, Math.max(1, Number(opts(p).columns) || 2))}>{render(p.children)}</div>),
  Card: r((p, render) => {
    const o = opts(p);
    return (
      <section className="gu-card" data-tone={o.tone ? tone(o.tone) : undefined}>
        {o.title || o.badge ? (
          <header className="gu-card-head">
            <div>
              {o.title ? <h4 className="gu-card-title">{text(o.title)}</h4> : null}
              {o.subtitle ? <div className="gu-caption">{text(o.subtitle)}</div> : null}
            </div>
            {o.badge ? <span className="gu-badge" data-tone={tone(o.tone)}>{text(o.badge)}</span> : null}
          </header>
        ) : null}
        {render(p.children)}
      </section>
    );
  }),
  Tab: r((p, render) => <>{render(p.children)}</>),
  Tabs: r((p, render, id) => <TabsView tabs={Array.isArray(p.tabs) ? p.tabs : []} render={render} sid={id} />),
  Details: r((p, render, id) => <DetailsView p={p} sid={id} render={render} />),
  Divider: r((p) => (p.label ? <div className="gu-divider" role="separator"><span>{text(p.label)}</span></div> : <hr className="gu-divider" />)),
  Heading: r((p) => {
    const level = Math.min(4, Math.max(2, Number(opts(p).level) || 3));
    const Tag = (`h${level + 1}`) as "h3";
    return <Tag className="gu-heading" data-level={level}>{text(p.text)}</Tag>;
  }),
  Text: r((p) => <p className={cn("gu-text", opts(p).tone === "muted" && "muted")}>{text(p.text)}</p>),
  Callout: r((p) => {
    const o = opts(p);
    return (
      <div role="note" className="gu-callout" data-tone={tone(o.tone ?? "info")}>
        {o.title ? <strong>{text(o.title)}</strong> : null}
        <span>{text(p.text)}</span>
      </div>
    );
  }),
  Badge: r((p) => <span className="gu-badge" data-tone={tone(opts(p).tone)}>{text(p.text)}</span>),
  Quote: r((p) => <QuoteView p={p} />),
  List: r((p) => <ListView p={p} />),
  KeyValue: r((p) => <KeyValueView p={p} />),
  Code: r((p) => <CodeView p={p} />),
  Metric: r((p) => <MetricView p={p} />),
  Progress: r((p) => <ProgressView p={p} />),
  Gauge: r((p) => <GaugeView p={p} />),
  ShareBar: r((p) => <ShareBarView p={p} />),
  Highlights: r((p) => <HighlightsView p={p} />),
  Series: r(() => null),
  LineChart: r((p, _, id) => <SeriesChart p={p} sid={id} kind="line" />),
  AreaChart: r((p, _, id) => <SeriesChart p={p} sid={id} kind="area" />),
  BarChart: r((p, _, id) => <SeriesChart p={p} sid={id} kind="bar" />),
  ComboChart: r((p, _, id) => <ComboView p={p} sid={id} />),
  PieChart: r((p) => {
    const o = opts(p);
    return pieData(p.labels, p.values) ? <PieChartView title={text(p.title)} labels={p.labels.map(text)} values={p.values} unit={text(o.unit) || undefined} decimals={o.decimals} /> : <Malformed />;
  }),
  ScatterChart: more("ScatterChart"),
  RadarChart: more("RadarChart"),
  Histogram: more("Histogram"),
  BoxPlot: more("BoxPlot"),
  Heatmap: more("Heatmap"),
  Waterfall: more("Waterfall"),
  Funnel: more("Funnel"),
  Treemap: more("Treemap"),
  Sankey: more("Sankey"),
  Candlestick: more("Candlestick"),
  Dumbbell: more("Dumbbell"),
  Timeline: r((p) => <TimelineView p={p} />),
  Steps: r((p) => <StepsView p={p} />),
  Checklist: r((p) => <ChecklistView p={p} />),
  Gantt: diagram("Gantt"),
  Table: r((p, _, id) => <TableView p={p} sid={id} />),
  Compare: r((p) => <CompareView p={p} />),
  Ranking: r((p) => <RankingView p={p} />),
  ProsCons: r((p) => <ProsConsView p={p} />),
  EntityCard: r((p) => <EntityCardView p={p} />),
  Flow: diagram("Flow"),
  Tree: diagram("Tree"),
  Diff: diagram("Diff"),
  FileTree: diagram("FileTree"),
  JsonView: diagram("JsonView"),
  Select: r((p) => <SelectView p={p} />),
  Slider: r((p) => <SliderView p={p} />),
  Toggle: r((p) => <ToggleView p={p} />),
  Input: r((p) => <InputView p={p} />),
  Button: r((p) => <ButtonView p={p} />),
  HtmlArtifact: r((p) => <HtmlArtifactView title={text(p.title)} document={typeof p.document === "string" ? p.document : ""} fallbackText={text(p.fallbackText)} />),
};

/** Data of the wrong shape (a well-formed program can compute one) shows as malformed, never as an empty or shifted
 * chart; the eval checker decides with the same shapeOk. */
const checked = (name: string, impl: ComponentRenderer<P>): ComponentRenderer<P> =>
  function Checked(args) {
    const Impl = impl;
    return shapeOk(name, args.props) ? <Impl {...args} /> : <Malformed />;
  };

export const library = createLibrary({
  components: DEFINITIONS.map((d) => defineComponent({ name: d.name, description: d.description, props: d.props as any, component: checked(d.name, IMPL[d.name]) })),
  root: "Stack",
});

