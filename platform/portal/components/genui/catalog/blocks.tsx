"use client";

// The core semantic components: metrics and the facts derived from data, lists, timelines, steps, comparisons and
// cards. They take raw data and compute what they show (data.ts), so a model never states a rank or a change itself.
import { useEffect, useMemo, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import { cn } from "../../../lib/cn";
import CopyButton from "../../copy-button";
import TableFrame from "../../table-frame";
import { IconCheck, IconStar, IconX } from "../../icons";
import { Sparkline } from "./charts";
import {
  changeRate, factValue, facts, formatNumber, formatRate, formatShare, goodness, numbers, objects, pieData, ranking, strings, text, winners, withUnit,
} from "./data";
import { Frame, Malformed, color } from "./kit";

type P = Record<string, any>;
const TONES = new Set(["neutral", "success", "warning", "danger", "info"]);
export const tone = (t: unknown, fallback = "neutral") => (typeof t === "string" && TONES.has(t) ? t : fallback);
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 });
const cellText = (v: unknown, decimals?: unknown) => (typeof v === "boolean" ? null : formatNumber(v, decimals));

function Bool({ on }: { on: boolean }) {
  return on ? <span className="gu-yes" aria-label="是"><IconCheck size={15} /></span> : <span className="gu-no" aria-label="否"><IconX size={15} /></span>;
}
/** A cell of a comparison or a field: true/false as marks, numbers formatted, the unit after them. */
function Val({ v, unit, decimals }: { v: unknown; unit?: unknown; decimals?: unknown }) {
  if (typeof v === "boolean") return <Bool on={v} />;
  const s = cellText(v, decimals);
  if (!s) return <span className="gu-faint">—</span>;
  return <>{s}{unit && typeof v === "number" ? <span className="gu-unit">{text(unit)}</span> : null}</>;
}

export function MetricView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const rate = changeRate(p.value, o.previous) ?? (typeof o.change === "number" && Number.isFinite(o.change) ? o.change : null);
  const trend = numbers(o.trend, 120);
  // a headline number past eight digits reads as 12.3亿; the exact value stays in its tooltip and in copies
  const big = typeof p.value === "number" && Math.abs(p.value) >= 1e8;
  return (
    <div className="gu-metric">
      <span className="gu-metric-label">{text(p.label)}</span>
      <span className="gu-metric-value">
        <span className="gu-metric-number" title={big ? formatNumber(p.value, o.decimals) : undefined}>{big ? compact.format(p.value) : formatNumber(p.value, o.decimals)}</span>
        {o.unit ? <span className="gu-unit">{text(o.unit)}</span> : null}
        {trend ? <Sparkline values={trend} better={o.better} /> : null}
      </span>
      {rate !== null ? (
        <span className="gu-delta" data-sign={goodness(rate, o.better)}>
          {formatRate(rate)}
          {typeof o.previous === "number" ? <span className="gu-faint"> · 上期 {formatNumber(o.previous, o.decimals)}</span> : null}
        </span>
      ) : null}
      {o.hint ? <span className="gu-caption">{text(o.hint)}</span> : null}
    </div>
  );
}

export function ProgressView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const max = typeof o.max === "number" && o.max > 0 ? o.max : 100;
  const v = typeof p.value === "number" && Number.isFinite(p.value) ? p.value : 0;
  const pct = Math.min(100, Math.max(0, (v / max) * 100));
  const target = typeof o.target === "number" ? Math.min(100, Math.max(0, (o.target / max) * 100)) : null;
  const plainPercent = max === 100 && !o.unit;
  return (
    <div className="gu-progress" data-tone={tone(o.tone)}>
      <div className="gu-field-head">
        <span>{text(p.label)}</span>
        <output>{plainPercent ? `${formatNumber(Math.round(v * 10) / 10)}%` : <>{formatNumber(v, o.decimals)}<span className="gu-faint"> / {withUnit(formatNumber(max, o.decimals), o.unit)} · {formatShare(v, max)}</span></>}</output>
      </div>
      <div className="gu-progress-track" role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={max} aria-label={text(p.label)}>
        <div style={{ width: `${pct}%` }} />
        {target !== null ? <i className="gu-progress-target" style={{ left: `${target}%` }} title={`目标 ${formatNumber(o.target, o.decimals)}`} /> : null}
      </div>
    </div>
  );
}

export function GaugeView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const min = typeof o.min === "number" ? o.min : 0;
  const max = typeof o.max === "number" && o.max > min ? o.max : 100;
  const v = typeof p.value === "number" && Number.isFinite(p.value) ? p.value : min;
  const t = Math.min(1, Math.max(0, (v - min) / (max - min)));
  const r = 52;
  const arc = (from: number, to: number) => {
    const a0 = Math.PI * (1 - from);
    const a1 = Math.PI * (1 - to);
    return `M${60 + r * Math.cos(a0)},${62 - r * Math.sin(a0)} A${r},${r} 0 0 1 ${60 + r * Math.cos(a1)},${62 - r * Math.sin(a1)}`;
  };
  const target = typeof o.target === "number" ? Math.min(1, Math.max(0, (o.target - min) / (max - min))) : null;
  // past the middle of the range reads as bad when lower is better
  const state = o.better === "low" ? (t > 0.8 ? "bad" : t > 0.6 ? "warn" : "good") : o.better === "high" ? (t < 0.2 ? "bad" : t < 0.4 ? "warn" : "good") : "neutral";
  return (
    <div className="gu-gauge" data-state={state} role="meter" aria-valuenow={v} aria-valuemin={min} aria-valuemax={max} aria-label={text(p.label)}>
      <svg viewBox="0 0 120 70" aria-hidden="true">
        <path d={arc(0, 1)} className="gu-gauge-track" />
        {t > 0 ? <path d={arc(0, t)} className="gu-gauge-fill" /> : null}
        {target !== null ? <line x1={60 + (r - 9) * Math.cos(Math.PI * (1 - target))} y1={62 - (r - 9) * Math.sin(Math.PI * (1 - target))} x2={60 + (r + 9) * Math.cos(Math.PI * (1 - target))} y2={62 - (r + 9) * Math.sin(Math.PI * (1 - target))} className="gu-gauge-target" /> : null}
      </svg>
      <div className="gu-gauge-value"><span className="gu-metric-number">{formatNumber(v, o.decimals)}</span>{o.unit ? <span className="gu-unit">{text(o.unit)}</span> : null}</div>
      <div className="gu-metric-label">{text(p.label)}</div>
    </div>
  );
}

export function ShareBarView({ p }: { p: P }) {
  const o: P = p.opts || {};
  if (!pieData(p.labels, p.values)) return <Malformed />;
  const labels = (p.labels as unknown[]).map(text);
  const values = p.values as number[];
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <Frame title={text(p.title)} unit={o.unit}>
      <div className="gu-share" role="img" aria-label={`${text(p.title)}：${labels.map((l, i) => `${l} ${formatShare(values[i], total)}`).join("，")}`}>
        {values.map((v, i) => (v > 0 ? <span key={i} style={{ flexGrow: v, background: color(i) }} title={`${labels[i]} ${formatShare(v, total)}`} /> : null))}
      </div>
      <ul className="gu-share-legend">
        {labels.map((l, i) => (
          <li key={i}><span className="gu-swatch" style={{ background: color(i) }} /><span>{l}</span><b>{formatShare(values[i], total)}</b><span className="gu-faint">{formatNumber(values[i], o.decimals)}</span></li>
        ))}
      </ul>
    </Frame>
  );
}

export function HighlightsView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const labels = strings(p.labels);
  const values = numbers(p.values);
  const list = useMemo(() => (labels && values ? facts(labels, values, Array.isArray(p.facts) ? p.facts : []) : []), [labels?.join("\u0000"), values?.join(","), JSON.stringify(p.facts)]);
  if (!labels || !values) return <Malformed />;
  if (!list.length) return null;
  return (
    <section className="gu-facts" aria-label={text(o.title) || "要点"}>
      {o.title ? <h4 className="gu-card-title">{text(o.title)}</h4> : null}
      <dl>
        {list.map((f) => (
          <div key={f.key} className="gu-fact" data-sign={f.signed ? goodness(f.value, o.better) : undefined}>
            <dt>{f.label}{f.at ? <span className="gu-fact-at">{f.at}</span> : null}</dt>
            <dd>{factValue({ ...f, rate: undefined }, o.unit, o.decimals)}{f.rate != null ? <span className="gu-fact-rate">{formatRate(f.rate)}</span> : null}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function KeyValueView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const items = objects<P>(p.items, ["label"]);
  if (!items) return <Malformed />;
  const cols = Math.min(3, Math.max(1, Number(o.columns) || (items.length > 6 ? 2 : 1)));
  return (
    <dl className="gu-kv" data-cols={cols}>
      {items.map((it, i) => (
        <div key={i}><dt>{text(it.label)}</dt><dd><Val v={it.value} unit={it.unit} decimals={o.decimals} /></dd></div>
      ))}
    </dl>
  );
}

export function ListView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const items = strings(p.items, 200);
  if (!items) return <Malformed />;
  const Tag = o.ordered ? "ol" : "ul";
  const marker = o.marker === "check" || o.marker === "cross" || o.marker === "arrow" ? o.marker : null;
  return (
    <Tag className={cn("gu-list", marker && "marked")} data-marker={marker ?? undefined}>
      {items.map((t, i) => (
        <li key={i}>
          {marker === "check" ? <Bool on /> : marker === "cross" ? <Bool on={false} /> : marker === "arrow" ? <span className="gu-faint" aria-hidden="true">→</span> : null}
          <span>{t}</span>
        </li>
      ))}
    </Tag>
  );
}

export function QuoteView({ p }: { p: P }) {
  const o: P = p.opts || {};
  return (
    <figure className="gu-quote">
      <blockquote>{text(p.text)}</blockquote>
      {o.source ? <figcaption>{text(o.source)}</figcaption> : null}
    </figure>
  );
}

export function CodeView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const code = text(p.code);
  return (
    <div className="md-codeblock gu-code">
      <div className="md-code-head">
        <span>{text(o.title) || text(o.language) || "代码"}</span>
        <CopyButton text={code} label="复制代码" />
      </div>
      <pre className="md-code"><code>{code}</code></pre>
    </div>
  );
}

export function TimelineView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const events = objects<P>(p.events, ["title"]);
  if (!events) return <Malformed />;
  const list = o.order === "desc" ? [...events].reverse() : events;
  return (
    <ol className="gu-timeline">
      {list.map((e, i) => (
        <li key={i} data-tone={tone(e.tone)}>
          <time>{text(e.date)}</time>
          <div><strong>{text(e.title)}</strong>{e.text ? <p>{text(e.text)}</p> : null}</div>
        </li>
      ))}
    </ol>
  );
}

const STATUS = new Set(["done", "current", "todo", "error"]);
export function StepsView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const steps = objects<P>(p.steps, ["title"]);
  if (!steps) return <Malformed />;
  const current = Number.isInteger(o.current) ? Number(o.current) : null;
  const status = (s: P, i: number) => (STATUS.has(s.status) ? s.status : current === null ? "todo" : i < current ? "done" : i === current ? "current" : "todo");
  return (
    <ol className="gu-steps" data-direction={o.direction === "horizontal" ? "horizontal" : "vertical"}>
      {steps.map((s, i) => {
        const st = status(s, i);
        return (
          <li key={i} data-status={st} aria-current={st === "current" ? "step" : undefined}>
            <span className="gu-step-dot">{st === "done" ? <IconCheck size={13} /> : st === "error" ? <IconX size={13} /> : i + 1}</span>
            <div><strong>{text(s.title)}</strong>{s.text ? <p>{text(s.text)}</p> : null}</div>
          </li>
        );
      })}
    </ol>
  );
}

export function ChecklistView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const items = objects<P>(p.items, ["text"]);
  if (!items) return <Malformed />;
  const done = items.filter((it) => it.done === true).length;
  return (
    <section className="gu-checklist">
      <div className="gu-field-head"><span>{text(o.title)}</span><output>{done} / {items.length}</output></div>
      <ul>
        {items.map((it, i) => (
          <li key={i} data-done={it.done === true}>
            <Bool on={it.done === true} />
            <span>{text(it.text)}{it.note ? <span className="gu-faint"> · {text(it.note)}</span> : null}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function CompareView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const options = strings(p.options, 8);
  const rows = objects<P>(p.rows, ["label", "values"]);
  if (!options || !rows || !rows.every((r) => Array.isArray(r.values))) return <Malformed />;
  const recommend = options.indexOf(text(o.recommend));
  return (
    <TableFrame>
    <div className="md-table gu-table gu-compare">
      <table>
        <thead>
          <tr>
            <th />
            {options.map((name, i) => <th key={i} className={i === recommend ? "gu-pick" : undefined}>{name}{i === recommend ? <span className="gu-badge" data-tone="success">推荐</span> : null}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => {
            const best = winners(r.values, r.better);
            return (
              <tr key={ri}>
                <th scope="row">{text(r.label)}{r.unit ? <span className="gu-faint">（{text(r.unit)}）</span> : null}</th>
                {options.map((_, i) => (
                  <td key={i} className={cn(typeof r.values[i] === "number" && "num", best.has(i) && "gu-best", i === recommend && "gu-pick")}>
                    <Val v={r.values[i]} decimals={o.decimals} />
                    {best.has(i) ? <span className="sr-only">（最优）</span> : null}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </TableFrame>
  );
}

export function RankingView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const items = objects<P>(p.items, ["name", "value"]);
  if (!items || !items.every((it) => typeof it.value === "number")) return <Malformed />;
  const ranked = ranking(items as (P & { value: number })[], o.better, o.top);
  const max = Math.max(...ranked.map((it) => Math.abs(it.value)), 0) || 1;
  return (
    <Frame title={text(p.title)} unit={o.unit}>
      <ol className="gu-ranking">
        {ranked.map((it, i) => (
          <li key={i} data-rank={it.rank <= 3 ? it.rank : undefined}>
            <span className="gu-rank">{it.rank}</span>
            <span className="gu-rank-name">{text(it.name)}{it.note ? <span className="gu-faint"> · {text(it.note)}</span> : null}</span>
            <span className="gu-rank-value">{formatNumber(it.value, o.decimals)}</span>
            <span className="gu-rank-bar" aria-hidden="true"><i style={{ width: `${(Math.abs(it.value) / max) * 100}%` }} /></span>
          </li>
        ))}
      </ol>
      {items.length > ranked.length ? <div className="gu-caption">共 {items.length} 项，显示前 {ranked.length} 项</div> : null}
    </Frame>
  );
}

export function ProsConsView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const pros = strings(p.pros, 50);
  const cons = strings(p.cons, 50);
  if (!pros || !cons) return <Malformed />;
  return (
    <section className="gu-proscons">
      {o.title ? <h4 className="gu-card-title">{text(o.title)}</h4> : null}
      <div>
        <div data-tone="success"><strong>{text(o.prosLabel) || "优点"}</strong><ul>{pros.map((t, i) => <li key={i}><Bool on /><span>{t}</span></li>)}</ul></div>
        <div data-tone="danger"><strong>{text(o.consLabel) || "缺点"}</strong><ul>{cons.map((t, i) => <li key={i}><Bool on={false} /><span>{t}</span></li>)}</ul></div>
      </div>
    </section>
  );
}

export function EntityCardView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const fields = objects<P>(o.fields, ["label"]) ?? [];
  const tags = strings(o.tags, 12) ?? [];
  const rating = typeof o.rating === "number" && Number.isFinite(o.rating) ? Math.min(5, Math.max(0, o.rating)) : null;
  return (
    <section className={cn("gu-card gu-entity", o.highlight === true && "highlight")}>
      <header>
        <div>
          <h4 className="gu-card-title">{text(p.name)}</h4>
          {o.subtitle ? <div className="gu-caption">{text(o.subtitle)}</div> : null}
        </div>
        {o.badge ? <span className="gu-badge" data-tone={o.highlight === true ? "success" : "neutral"}>{text(o.badge)}</span> : null}
      </header>
      {typeof o.price === "number" ? <div className="gu-entity-price"><span className="gu-metric-number">{formatNumber(o.price, o.decimals)}</span>{o.priceUnit ? <span className="gu-unit">{text(o.priceUnit)}</span> : null}</div> : null}
      {rating !== null ? (
        <div className="gu-rating" aria-label={`评分 ${formatNumber(rating)} / 5`}>
          {[0, 1, 2, 3, 4].map((i) => <IconStar key={i} size={14} className={rating >= i + 0.75 ? "on" : rating >= i + 0.25 ? "half" : undefined} />)}
          <span>{formatNumber(rating)}</span>
        </div>
      ) : null}
      {o.description ? <p className="gu-text muted">{text(o.description)}</p> : null}
      {fields.length ? <dl className="gu-kv" data-cols={1}>{fields.map((f, i) => <div key={i}><dt>{text(f.label)}</dt><dd><Val v={f.value} unit={f.unit} decimals={o.decimals} /></dd></div>)}</dl> : null}
      {tags.length ? <div className="gu-tags">{tags.map((t, i) => <span key={i} className="gu-badge">{t}</span>)}</div> : null}
    </section>
  );
}

/** A collapsible section; whether it is open is kept with the answer's other control state. */
export function DetailsView({ p, sid, render }: { p: P; sid?: string; render: (v: unknown) => React.ReactNode }) {
  const o: P = p.opts || {};
  const field = useStateField<boolean>(`details:${sid ?? text(p.title)}`.slice(0, 120));
  // paper shows every section: a closed <details> prints only its summary
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const before = () => setPrinting(true);
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  const open = printing || (typeof field.value === "boolean" ? field.value : o.open === true);
  return (
    <details className="gu-details" open={open} onToggle={(e) => { if (!printing && (e.currentTarget as HTMLDetailsElement).open !== open) field.setValue((e.currentTarget as HTMLDetailsElement).open); }}>
      <summary>{text(p.title)}</summary>
      <div className="gu-stack">{render(p.children)}</div>
    </details>
  );
}
