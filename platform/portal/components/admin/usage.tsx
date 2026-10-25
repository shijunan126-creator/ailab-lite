"use client";

import { useEffect, useState } from "react";
import { admin, reason, UsageRow } from "../../lib/admin";
import { formatTokens } from "../../lib/format";

type Range = "today" | "7d" | "month" | "last-month";
type Group = "dept" | "user" | "model";
const RANGES: [Range, string][] = [
  ["today", "今天"],
  ["7d", "近 7 天"],
  ["month", "本月"],
  ["last-month", "上月"],
];
const GROUPS: [Group, string][] = [
  ["dept", "部门"],
  ["user", "用户"],
  ["model", "模型"],
];

function bounds(range: Range, now = new Date()): { from: string; to: string } {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const from =
    range === "today"
      ? day
      : range === "7d"
        ? new Date(day.getTime() - 6 * 86_400_000)
        : range === "month"
          ? new Date(now.getFullYear(), now.getMonth(), 1)
          : new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const to = range === "last-month" ? new Date(now.getFullYear(), now.getMonth(), 1) : now;
  return { from: from.toISOString(), to: to.toISOString() };
}

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map(([k, text]) => (
        <button key={k} role="radio" aria-checked={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export default function UsageSection() {
  const [range, setRange] = useState<Range>("month");
  const [group, setGroup] = useState<Group>("user");
  const [rows, setRows] = useState<UsageRow[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stale = false;
    setRows(null);
    admin
      .usage({ ...bounds(range), group_by: group })
      .then((r) => !stale && (setRows([...r.rows].sort((a, b) => b.weighted - a.weighted)), setError("")))
      .catch((err) => !stale && setError(reason(err)));
    return () => {
      stale = true;
    };
  }, [range, group]);

  const top = Math.max(1, ...(rows || []).map((r) => r.weighted));
  const total = (rows || []).reduce((sum, r) => sum + r.weighted, 0);
  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <Segmented<Range> label="时间" value={range} options={RANGES} onChange={setRange} />
        <Segmented<Group> label="分组" value={group} options={GROUPS} onChange={setGroup} />
        <span className="spacer" />
        {rows ? <span className="usage-total">{formatTokens(total)}</span> : null}
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {rows?.length === 0 ? <div className="empty">这段时间没有用量</div> : null}
      <div className="usage-bars">
        {(rows || []).map((r) => (
          <div key={r.key} className="usage-bar" title={`输入 ${r.input_tokens.toLocaleString()} · 输出 ${r.output_tokens.toLocaleString()} · ${r.calls} 次调用`}>
            <span className="label">{r.label || "未分配"}</span>
            <span className="track">
              <span className="fill" style={{ width: `${(r.weighted / top) * 100}%` }} />
            </span>
            <span className="value">{formatTokens(r.weighted)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
