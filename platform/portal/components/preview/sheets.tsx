"use client";

import { useEffect, useState } from "react";

type Cell = { v: string; b?: 1; bg?: string; fg?: string; n?: 1; f?: string };
type Sheet = { name: string; rows: Cell[][]; widths: (number | null)[]; truncated: boolean; size: [number, number]; charts: number };

function columnName(i: number): string {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

/** A workbook rendered as sheets.json: by a run's renderer, or the library's. */
export default function Sheets({ src }: { src: string | null }) {
  const [data, setData] = useState<{ sheets: Sheet[]; errors?: string[] } | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState(0);
  useEffect(() => {
    if (!src) return;
    let stale = false;
    fetch(src)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d) => !stale && (setData(d), setError("")))
      .catch((e) => !stale && setError(String(e?.message || e)));
    return () => {
      stale = true;
    };
  }, [src]);
  if (error) return <div className="alert error">预览加载失败：{error}</div>;
  if (!data) return <div className="hint">加载中…</div>;
  const sheet = data.sheets[Math.min(tab, data.sheets.length - 1)];
  if (!sheet) return <div className="hint">空工作簿</div>;
  const cols = Math.max(0, ...sheet.rows.map((r) => r.length));
  return (
    <div className="sheets">
      {data.errors?.length ? <div className="alert error">公式错误：{data.errors.slice(0, 5).join("，")}</div> : null}
      <div className="sheet-grid">
        <table>
          <thead>
            <tr>
              <th className="corner" />
              {Array.from({ length: cols }, (_, j) => (
                <th key={j} style={{ minWidth: Math.max(48, Math.min(320, (sheet.widths[j] || 9) * 7.5)) }}>
                  {columnName(j)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.rows.map((row, i) => (
              <tr key={i}>
                <th>{i + 1}</th>
                {Array.from({ length: cols }, (_, j) => {
                  const c = row[j] || { v: "" };
                  return (
                    <td
                      key={j}
                      title={c.f || undefined}
                      className={`${c.n ? "num" : ""} ${c.f ? "formula" : ""}`}
                      style={{ background: c.bg ? `#${c.bg}` : undefined, color: c.fg ? `#${c.fg}` : undefined, fontWeight: c.b ? 650 : undefined }}
                    >
                      {c.v}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sheet-tabs">
        {data.sheets.map((s, i) => (
          <button key={s.name} className={`sheet-tab ${i === tab ? "on" : ""}`} onClick={() => setTab(i)}>
            {s.name}
          </button>
        ))}
        <span className="hint">
          {sheet.truncated ? `只显示前 ${sheet.rows.length} 行；共 ${sheet.size[0]} 行 × ${sheet.size[1]} 列` : ""}
          {sheet.charts ? ` · 含 ${sheet.charts} 个图表（下载后查看）` : ""}
        </span>
      </div>
    </div>
  );
}
