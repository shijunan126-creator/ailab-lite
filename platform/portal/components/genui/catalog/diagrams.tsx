"use client";

// Diagrams and code views, in their own chunk: flows laid out in layers, trees, schedules, diffs, file trees and
// JSON. The model gives nodes and edges, tasks and dates, before and after; the layout is computed here.
import { useMemo, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import { cn } from "../../../lib/cn";
import CopyButton from "../../copy-button";
import { IconChevronRight, IconFile, IconFolder } from "../../icons";
import { dayOf, lineDiff, objects, pathTree, strings, text, treeOf, type PathNode, type TreeNode } from "./data";
import { Frame, Legend, Malformed, color, fit, textWidth, useWidth } from "./kit";

type P = Record<string, any>;
const o = (p: P): P => p.opts || {};
const TONE_FILL: Record<string, string> = { success: "var(--success-soft)", warning: "var(--warning-soft)", danger: "var(--destructive-soft)", info: "var(--info-soft)" };
const TONE_STROKE: Record<string, string> = { success: "var(--success)", warning: "var(--warning)", danger: "var(--destructive)", info: "var(--info)" };

// ---------- flow ----------

type FlowNode = { id: string; label: string; tone?: string };
type FlowEdge = { from: string; to: string; label?: string };

/** Layers by longest path from the sources (edges that close a cycle are drawn but do not push layers), then each
 * layer ordered by the mean position of its parents to cut crossings. */
function layer(nodes: FlowNode[], edges: FlowEdge[]) {
  const ids = nodes.map((n) => n.id);
  const rank = new Map(ids.map((id) => [id, 0]));
  const forward = edges.filter((e) => rank.has(e.from) && rank.has(e.to) && e.from !== e.to);
  // depth-first order marks the edges that go back to an ancestor
  const state = new Map<string, number>();
  const back = new Set<FlowEdge>();
  const visit = (id: string) => {
    state.set(id, 1);
    for (const e of forward.filter((x) => x.from === id)) {
      if (state.get(e.to) === 1) back.add(e);
      else if (!state.get(e.to)) visit(e.to);
    }
    state.set(id, 2);
  };
  ids.forEach((id) => state.get(id) || visit(id));
  const dag = forward.filter((e) => !back.has(e));
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const e of dag) if (rank.get(e.to)! < rank.get(e.from)! + 1) { rank.set(e.to, rank.get(e.from)! + 1); moved = true; }
    if (!moved) break;
  }
  const depth = Math.max(0, ...rank.values()) + 1;
  const layers = Array.from({ length: depth }, (_, d) => ids.filter((id) => rank.get(id) === d));
  const at = new Map<string, number>();
  layers.forEach((l) => l.forEach((id, i) => at.set(id, i)));
  for (let d = 1; d < depth; d++) {
    const score = (id: string) => {
      const parents = dag.filter((e) => e.to === id).map((e) => at.get(e.from)!);
      return parents.length ? parents.reduce((a, b) => a + b, 0) / parents.length : at.get(id)!;
    };
    layers[d].sort((a, b) => score(a) - score(b));
    layers[d].forEach((id, i) => at.set(id, i));
  }
  return { rank, layers, back, edges: forward };
}

function FlowView({ p }: { p: P }) {
  const opt = o(p);
  const nodes = objects<FlowNode>(p.nodes, ["id"], 60);
  const edges = objects<FlowEdge>(p.edges, ["from", "to"], 120);
  const { ref, width } = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<string | null>(null);
  const data = useMemo(() => {
    if (!nodes || !edges) return null;
    const list = nodes.map((n) => ({ id: text(n.id), label: text(n.label) || text(n.id), tone: text(n.tone) }));
    return { list, ...layer(list, edges.map((e) => ({ from: text(e.from), to: text(e.to), label: e.label == null ? undefined : text(e.label) }))) };
  }, [JSON.stringify(nodes), JSON.stringify(edges)]);
  if (!data || !data.list.length) return <Malformed />;
  const { list, rank, layers, back } = data;
  const maxRow = Math.max(...layers.map((l) => l.length));
  // flowing right needs room for every layer side by side; on a narrow column it flows down instead
  const nodeW = Math.min(150, Math.max(72, ...list.map((n) => Math.min(150, textWidth(n.label) + 24))));
  const right = opt.direction !== "down" && width >= layers.length * (nodeW + 36);
  const nodeH = 36;
  // positions are worked out along the flow (main) and across it (cross), then mapped to x/y
  const mainSize = right ? nodeW : nodeH;
  const crossSize = right ? nodeH : nodeW;
  const backward = (e: FlowEdge) => back.has(e) || rank.get(e.to)! <= rank.get(e.from)!;
  const anyBack = data.edges.some(backward);
  // a backward edge leaves and enters through the gaps before the first and after the last layer
  const lead = anyBack ? 18 : 4;
  const gapMain = right ? Math.max(28, (width - 2 * lead - layers.length * nodeW) / Math.max(1, layers.length - 1)) : 44;
  const gapCross = 14;
  const crossTotal = maxRow * crossSize + (maxRow - 1) * gapCross;
  const mainTotal = 2 * lead + layers.length * mainSize + (layers.length - 1) * gapMain;
  const place = new Map<string, { m: number; c: number }>();
  layers.forEach((l, d) => {
    const used = l.length * crossSize + (l.length - 1) * gapCross;
    l.forEach((id, i) => place.set(id, { m: lead + d * (mainSize + gapMain), c: 4 + (crossTotal - used) / 2 + i * (crossSize + gapCross) }));
  });
  // a backward edge runs beyond every node of the layers it spans, so it never crosses a box
  const bends = new Map<FlowEdge, number>();
  for (const e of data.edges.filter(backward)) {
    const lo = Math.min(rank.get(e.from)!, rank.get(e.to)!);
    const hi = Math.max(rank.get(e.from)!, rank.get(e.to)!);
    const spanned = list.filter((n) => rank.get(n.id)! >= lo && rank.get(n.id)! <= hi);
    bends.set(e, Math.max(...spanned.map((n) => place.get(n.id)!.c)) + crossSize + 18 + 10 * bends.size);
  }
  const crossEnd = Math.max(crossTotal + 8, ...[...bends].map(([e, b]) => b + (e.label ? (right ? 18 : Math.min(90, textWidth(e.label)) + 10) : 8)));
  const W = right ? Math.max(width, mainTotal) : Math.max(width, crossEnd);
  const H = right ? crossEnd : mainTotal;
  // flowing down, the drawing sits in the middle of a wide column
  const shift = right ? 0 : (W - crossEnd) / 2;
  const xy = (m: number, c: number): [number, number] => (right ? [m, c] : [c + shift, m]);
  const pos = new Map([...place].map(([id, q]) => { const [x, y] = xy(q.m, q.c); return [id, { x, y }] as const; }));
  /** A polyline in main/cross coordinates with rounded corners. */
  const route = (pts: [number, number][]) => {
    const P = pts.map(([m, c]) => xy(m, c));
    let d = `M${P[0][0]},${P[0][1]}`;
    for (let k = 1; k < P.length - 1; k++) {
      const [px, py] = P[k - 1];
      const [x, y] = P[k];
      const [nx, ny] = P[k + 1];
      const r = Math.min(8, Math.hypot(x - px, y - py) / 2, Math.hypot(nx - x, ny - y) / 2);
      const u = (ax: number, ay: number, t: number) => { const l = Math.hypot(ax, ay) || 1; return [x + (ax / l) * t, y + (ay / l) * t]; };
      const [ax, ay] = u(px - x, py - y, r);
      const [bx, by] = u(nx - x, ny - y, r);
      d += `L${ax},${ay}Q${x},${y} ${bx},${by}`;
    }
    const [lx, ly] = P[P.length - 1];
    return d + `L${lx},${ly}`;
  };
  const byId = new Map(list.map((n) => [n.id, n]));
  const linked = (e: FlowEdge) => at != null && (e.from === at || e.to === at);
  return (
    <Frame title={text(opt.title)}>
      <div ref={ref} className="gu-plot gu-scroll-x">
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={text(opt.title) || "流程图"} onPointerLeave={() => setAt(null)}>
          <defs>
            <marker id="gu-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--muted-foreground)" /></marker>
          </defs>
          {data.edges.map((e, i) => {
            const a = place.get(e.from);
            const b = place.get(e.to);
            if (!a || !b) return null;
            const bend = bends.get(e);
            let d: string;
            let lm: number;
            let lc: number;
            if (bend != null) {
              // out past the source's layer, around, and in before the target's layer, off-centre so forward arrows stay clear
              const c0 = a.c + crossSize * 0.75;
              const c1 = b.c + crossSize * 0.75;
              const m0 = a.m + mainSize;
              const m1 = b.m;
              d = route([[m0, c0], [m0 + lead - 4, c0], [m0 + lead - 4, bend], [m1 - lead + 4, bend], [m1 - lead + 4, c1], [m1 - 2, c1]]);
              lm = (m0 + m1) / 2;
              lc = bend + (right ? 14 : 6);
            } else {
              const c0 = a.c + crossSize / 2;
              const c1 = b.c + crossSize / 2;
              const m0 = a.m + mainSize;
              const m1 = b.m;
              const [x0, y0] = xy(m0, c0);
              const [x1, y1] = xy(m1 - 2, c1);
              const mid = (m0 + m1) / 2;
              const [qx0, qy0] = xy(mid, c0);
              const [qx1, qy1] = xy(mid, c1);
              d = `M${x0},${y0}C${qx0},${qy0} ${qx1},${qy1} ${x1},${y1}`;
              lm = mid + (right ? 0 : 4);
              lc = (c0 + c1) / 2 + (right ? -8 : -6);
            }
            const [lx, ly] = xy(lm, lc);
            const isBack = bend != null;
            return (
              <g key={i} className={at != null && !linked(e) ? "gu-dim" : undefined}>
                <path d={d} fill="none" stroke={linked(e) ? "var(--foreground)" : "var(--muted-foreground)"} strokeWidth={1.4} strokeDasharray={isBack ? "5 4" : undefined} markerEnd="url(#gu-arrow)" />
                {e.label ? <text x={lx} y={ly} textAnchor={right ? "middle" : isBack ? "start" : "end"} dominantBaseline="middle" className="gu-flow-edge">{fit(e.label, 90)}</text> : null}
              </g>
            );
          })}
          {list.map((n) => {
            const q = pos.get(n.id)!;
            return (
              <g key={n.id} onPointerEnter={() => setAt(n.id)} className={at != null && at !== n.id && !data.edges.some((e) => linked(e) && (e.from === n.id || e.to === n.id)) ? "gu-dim" : undefined}>
                <rect x={q.x} y={q.y} width={nodeW} height={nodeH} rx={8} fill={TONE_FILL[n.tone ?? ""] ?? "var(--popover)"} stroke={TONE_STROKE[n.tone ?? ""] ?? "var(--border-strong)"} />
                <text x={q.x + nodeW / 2} y={q.y + nodeH / 2 + 4} textAnchor="middle" className="gu-flow-node">{fit(byId.get(n.id)!.label, nodeW - 14)}</text>
                <title>{n.label}</title>
              </g>
            );
          })}
        </svg>
      </div>
    </Frame>
  );
}

// ---------- tree ----------

function TreeBranch({ node, depth, open, path, toggle }: { node: TreeNode; depth: number; open: (path: string, depth: number) => boolean; path: string; toggle: (path: string, depth: number) => void }) {
  const has = node.children.length > 0;
  const shown = has && open(path, depth);
  return (
    <li>
      <div className="gu-tree-row">
        {has ? (
          <button type="button" className="gu-tree-toggle" aria-expanded={shown} onClick={() => toggle(path, depth)} aria-label={shown ? "收起" : "展开"}>
            <IconChevronRight size={14} />
          </button>
        ) : <span className="gu-tree-leaf" aria-hidden="true" />}
        <span className={cn("gu-tree-name", depth === 0 && "root")}>{node.name}</span>
        {node.note ? <span className="gu-faint">{node.note}</span> : null}
        {has && !shown ? <span className="gu-faint">· {node.children.length}</span> : null}
      </div>
      {shown ? <ul>{node.children.map((c, i) => <TreeBranch key={i} node={c} depth={depth + 1} open={open} path={`${path}.${i}`} toggle={toggle} />)}</ul> : null}
    </li>
  );
}

function TreeView({ p, sid }: { p: P; sid?: string }) {
  const opt = o(p);
  const root = useMemo(() => treeOf(p.root), [JSON.stringify(p.root)]);
  const depth = Number.isInteger(opt.depth) ? Number(opt.depth) : 3;
  // the paths the reader flipped from their default, saved with the answer
  const field = useStateField<string[]>(`tree:${sid ?? text(opt.title)}`.slice(0, 120));
  const flipped = new Set(Array.isArray(field.value) ? field.value : []);
  if (!root) return <Malformed />;
  const open = (path: string, d: number) => (d < depth) !== flipped.has(path);
  const toggle = (path: string) => {
    const next = new Set(flipped);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    field.setValue([...next].slice(0, 40));
  };
  return (
    <Frame title={text(opt.title)}>
      <ul className="gu-tree" role="tree"><TreeBranch node={root} depth={0} open={open} path="0" toggle={toggle} /></ul>
    </Frame>
  );
}

// ---------- gantt ----------

function GanttView({ p }: { p: P }) {
  const tasks = objects<{ name: unknown; start: unknown; end: unknown; group?: unknown; done?: unknown }>(p.tasks, ["name", "start", "end"], 80);
  const { ref, width } = useWidth<HTMLDivElement>();
  const rows = useMemo(() => (tasks ?? []).map((t) => ({ name: text(t.name), s: dayOf(t.start), e: dayOf(t.end), group: text(t.group), done: t.done === true, start: text(t.start), end: text(t.end) })), [JSON.stringify(tasks)]);
  if (!tasks || !rows.length || rows.some((r) => r.s == null || r.e == null)) return <Malformed />;
  const lo = Math.min(...rows.map((r) => r.s!));
  const hi = Math.max(...rows.map((r) => Math.max(r.e!, r.s!))) + 1;
  const groups = [...new Set(rows.map((r) => r.group))];
  const labelRoom = Math.min(140, Math.max(56, ...rows.map((r) => textWidth(r.name))) + 16);
  const rowH = 28;
  const head = 22;
  const H = head + rows.length * rowH + 4;
  const iw = Math.max(1, width - labelRoom - 8);
  const X = (d: number) => labelRoom + (iw * (d - lo)) / (hi - lo || 1);
  // month boundaries when the span is long, else weeks
  const span = hi - lo;
  const ticks: { d: number; label: string }[] = [];
  const date = (d: number) => new Date(d * 86400000);
  if (span > 70) {
    const first = date(lo);
    for (let m = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1)); m.getTime() / 86400000 < hi; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1)))
      ticks.push({ d: m.getTime() / 86400000, label: `${m.getUTCMonth() + 1}月` });
  } else {
    const stepDays = span > 21 ? 7 : span > 7 ? 2 : 1;
    for (let d = lo; d < hi; d += stepDays) ticks.push({ d, label: `${date(d).getUTCMonth() + 1}/${date(d).getUTCDate()}` });
  }
  const every = Math.max(1, Math.ceil(44 / Math.max(1, X(ticks[1]?.d ?? hi) - X(ticks[0]?.d ?? lo))));
  return (
    <Frame title={text(p.title)}>
      <div ref={ref} className="gu-plot">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={text(p.title) || "排期"}>
          {ticks.map((t, i) => (i % every === 0 ? (
            <g key={i}><line x1={X(t.d)} x2={X(t.d)} y1={head - 4} y2={H} className="gu-grid-line" />{/* a label that would run off the right edge ends at it */}<text x={X(t.d) + 3 + textWidth(t.label) > width ? width - 2 : X(t.d) + 3} y={14} textAnchor={X(t.d) + 3 + textWidth(t.label) > width ? "end" : "start"} className="gu-axis">{t.label}</text></g>
          ) : null))}
          {rows.map((r, i) => {
            const y = head + i * rowH;
            const x0 = X(r.s!);
            const x1 = X(Math.max(r.e!, r.s!) + 1);
            return (
              <g key={i}>
                <text x={labelRoom - 8} y={y + rowH / 2 + 4} textAnchor="end" className="gu-axis gu-axis-label">{fit(r.name, labelRoom - 14)}</text>
                <rect x={x0} y={y + 6} width={Math.max(3, x1 - x0)} height={rowH - 12} rx={4} fill={color(groups.length > 1 ? groups.indexOf(r.group) : 0)} fillOpacity={r.done ? 0.45 : 0.9}>
                  <title>{`${r.name}：${r.start} – ${r.end}`}</title>
                </rect>
              </g>
            );
          })}
        </svg>
      </div>
      {groups.length > 1 ? <Legend items={groups.map((g, i) => ({ name: g || "其它", color: color(i) }))} /> : null}
      <table className="sr-only"><caption>{text(p.title)}</caption><tbody>{rows.map((r, i) => <tr key={i}><th>{r.name}</th><td>{r.start}</td><td>{r.end}</td></tr>)}</tbody></table>
    </Frame>
  );
}

// ---------- code ----------

function DiffView({ p }: { p: P }) {
  const opt = o(p);
  const before = text(p.before);
  const after = text(p.after);
  const lines = useMemo(() => lineDiff(before, after), [before, after]);
  const added = lines.filter((l) => l.kind === "add").length;
  const removed = lines.filter((l) => l.kind === "del").length;
  return (
    <div className="md-codeblock gu-code gu-diff">
      <div className="md-code-head">
        <span>{text(opt.title) || text(opt.language) || "修改"}<span className="gu-diff-count"><b data-kind="add">+{added}</b> <b data-kind="del">−{removed}</b></span></span>
        <CopyButton text={after} label="复制修改后" />
      </div>
      <pre className="md-code"><code>{lines.map((l, i) => <span key={i} className="gu-diff-line" data-kind={l.kind}><i aria-hidden="true">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : " "}</i>{l.text || " "}{"\n"}</span>)}</code></pre>
    </div>
  );
}

function PathBranch({ nodes }: { nodes: PathNode[] }) {
  return (
    <ul>
      {nodes.map((n, i) => (
        <li key={i}>
          <span className="gu-file-row">{n.dir ? <IconFolder size={14} /> : <IconFile size={14} />}<span>{n.name}</span></span>
          {n.children.length ? <PathBranch nodes={n.children} /> : null}
        </li>
      ))}
    </ul>
  );
}

function FileTreeView({ p }: { p: P }) {
  const opt = o(p);
  const paths = strings(p.paths, 500);
  if (!paths) return <Malformed />;
  return <Frame title={text(opt.title)}><div className="gu-filetree"><PathBranch nodes={pathTree(paths)} /></div></Frame>;
}

function JsonNode({ k, v, depth, open }: { k?: string; v: unknown; depth: number; open: number }) {
  const [shown, setShown] = useState(depth < open);
  const key = k != null ? <span className="gu-json-key">{k}: </span> : null;
  if (v === null || typeof v !== "object") {
    const kind = v === null ? "null" : typeof v;
    return <div className="gu-json-row">{key}<span className="gu-json-value" data-kind={kind}>{typeof v === "string" ? JSON.stringify(v) : String(v)}</span></div>;
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as Record<string, unknown>);
  const [l, r] = Array.isArray(v) ? ["[", "]"] : ["{", "}"];
  return (
    <div className="gu-json-row">
      <button type="button" className="gu-tree-toggle" aria-expanded={shown} onClick={() => setShown(!shown)} aria-label={shown ? "收起" : "展开"}><IconChevronRight size={13} /></button>
      {key}<span className="gu-faint">{l}{shown ? "" : ` ${entries.length} 项 ${r}`}</span>
      {shown ? <div className="gu-json-children">{entries.slice(0, 200).map(([ck, cv]) => <JsonNode key={ck} k={Array.isArray(v) ? undefined : ck} v={cv} depth={depth + 1} open={open} />)}</div> : null}
      {shown ? <span className="gu-faint">{r}</span> : null}
    </div>
  );
}

function JsonViewView({ p }: { p: P }) {
  const opt = o(p);
  const json = useMemo(() => { try { return JSON.stringify(p.data, null, 2) ?? ""; } catch { return ""; } }, [p.data]);
  return (
    <div className="md-codeblock gu-code">
      <div className="md-code-head"><span>{text(opt.title) || "JSON"}</span><CopyButton text={json} label="复制 JSON" /></div>
      <div className="md-code gu-json"><JsonNode v={p.data} depth={0} open={Number.isInteger(opt.depth) ? Number(opt.depth) : 2} /></div>
    </div>
  );
}

const VIEWS: Record<string, (props: { p: P; sid?: string }) => React.ReactNode> = {
  Flow: FlowView,
  Tree: TreeView,
  Gantt: GanttView,
  Diff: DiffView,
  FileTree: FileTreeView,
  JsonView: JsonViewView,
};

export default function Diagram({ name, p, sid }: { name: string; p: P; sid?: string }) {
  const View = VIEWS[name];
  return View ? <View p={p} sid={sid} /> : null;
}
