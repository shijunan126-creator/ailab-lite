"use client";

import { useEffect, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { formatTokens } from "../../lib/format";
import Switch from "../switch";

type Band = { target_lower_tokens: number; target_aim_tokens: number; target_upper_tokens: number; hard_ceiling_tokens: number };
const bandFields = [["target_lower_tokens", "压缩后下限"], ["target_aim_tokens", "压缩后目标"], ["target_upper_tokens", "压缩后上限"], ["hard_ceiling_tokens", "压缩后硬限"]] as const;
type ContextSettings = Band & { auto_compact: boolean; threshold_ratio: number; models: { id: string; name: string; context_tokens: number }[] };

/** When a long conversation is summarised: at a share of each model's context window, the same rule for every model. */
export default function ContextSection() {
  const [saved, setSaved] = useState<ContextSettings | null>(null);
  const [on, setOn] = useState(false);
  const [ratio, setRatio] = useState(0.5);
  const [band, setBand] = useState<Band>({ target_lower_tokens: 10000, target_aim_tokens: 30000, target_upper_tokens: 48000, hard_ceiling_tokens: 64000 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api<ContextSettings>("/v1/admin/settings/context", { signal: controller.signal })
      .then((s) => {
        setSaved(s);
        setOn(s.auto_compact);
        setRatio(s.threshold_ratio);
        setBand(s);
      })
      .catch((err) => !controller.signal.aborted && setError(errorMessage(err)));
    return () => controller.abort();
  }, []);

  const dirty = !!saved && (on !== saved.auto_compact || Math.abs(ratio - saved.threshold_ratio) > 1e-6 || bandFields.some(([key]) => band[key] !== saved[key]));
  async function save() {
    setBusy(true);
    setError("");
    try {
      const next = await api<ContextSettings>("/v1/admin/settings/context", {
        method: "PUT",
        body: JSON.stringify({ ...Object.fromEntries(Object.entries(saved!).filter(([key]) => key !== "models")), ...Object.fromEntries(bandFields.map(([key]) => [key, band[key]])), auto_compact: on, threshold_ratio: Math.round(ratio * 100) / 100 }),
      });
      setBand(next);
      setSaved({ ...next, models: next.models?.length ? next.models : saved!.models });
      setDone(true);
      setTimeout(() => setDone(false), 1600);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!saved) return <section className="admin-section">{error ? <div className="alert error">{error}</div> : <div className="pr-loading"><span /><span /><span /></div>}</section>;
  const pct = Math.round(ratio * 100);
  return (
    <section className="admin-section context-settings">
      <div className="card card-pad">
        <div className="field-row">
          <span>自动压缩上下文</span>
          <Switch on={on} label="自动压缩上下文" onChange={setOn} />
        </div>
        <div className="collapse" data-open={on} inert={!on}>
          <div>
            <div className="context-threshold">
              <div className="context-threshold-head">
                <span>压缩阈值</span>
                <strong>{pct}%</strong>
              </div>
              <input type="range" min={20} max={95} step={5} value={pct} aria-label="压缩阈值" onChange={(e) => setRatio(Number(e.target.value) / 100)}
                style={{ "--fill": `${((pct - 20) / 75) * 100}%` } as React.CSSProperties} />
              {bandFields.map(([key, label]) => (
                <label className="field-row" key={key}>
                  <span>{label}（token）</span>
                  <input type="number" min={1} max={1000000} step={1000} aria-label={label} value={band[key]}
                    onChange={(event) => setBand({ ...band, [key]: Number(event.target.value) })} />
                </label>
              ))}
              {saved.models.length ? (
                <ul className="context-models">
                  {saved.models.map((m) => (
                    <li key={m.id}>
                      <span className="name">{m.name}</span>
                      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
                      <span className="num">{formatTokens(Math.round(m.context_tokens * ratio))} / {formatTokens(m.context_tokens)}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
        </div>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      <div className="context-foot">
        <button onClick={save} disabled={!dirty || busy}>{busy ? "保存中…" : done ? "已保存" : "保存"}</button>
        {dirty ? <button className="link" onClick={() => { setOn(saved.auto_compact); setRatio(saved.threshold_ratio); setBand(saved); }}>还原</button> : null}
      </div>
      <LibrarySettings />
    </section>
  );
}

/** A short summary of each library file, written once when the file is ready; it helps both people and the model pick a file. */
function LibrarySettings() {
  const [on, setOn] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<{ summaries: boolean }>("/v1/admin/settings/library", { signal: controller.signal })
      .then((s) => setOn(s.summaries))
      .catch((err) => !controller.signal.aborted && setError(errorMessage(err)));
    return () => controller.abort();
  }, []);
  async function toggle() {
    if (on === null) return;
    const next = !on;
    setOn(next);
    setError("");
    try {
      const saved = await api<{ summaries: boolean }>("/v1/admin/settings/library", { method: "PATCH", body: JSON.stringify({ summaries: next }) });
      setOn(saved.summaries);
    } catch (err) {
      setOn(!next);
      setError(errorMessage(err));
    }
  }
  return (
    <div className="card card-pad library-settings">
      <div className="field-row">
        <span>为资料库文件生成摘要</span>
        <Switch on={!!on} label="为资料库文件生成摘要" disabled={on === null} onChange={toggle} />
      </div>
      {error ? <div className="alert error">{error}</div> : null}
    </div>
  );
}
