"use client";

import { useEffect, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { formatTokens } from "../../lib/format";

type ContextSettings = { auto_compact: boolean; threshold_ratio: number; models: { id: string; name: string; context_tokens: number }[] };

/** When a long conversation is summarised: at a share of each model's context window, the same rule for every model. */
export default function ContextSection() {
  const [saved, setSaved] = useState<ContextSettings | null>(null);
  const [on, setOn] = useState(false);
  const [ratio, setRatio] = useState(0.5);
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
      })
      .catch((err) => !controller.signal.aborted && setError(errorMessage(err)));
    return () => controller.abort();
  }, []);

  const dirty = !!saved && (on !== saved.auto_compact || Math.abs(ratio - saved.threshold_ratio) > 1e-6);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const next = await api<ContextSettings>("/v1/admin/settings/context", {
        method: "PUT",
        body: JSON.stringify({ auto_compact: on, threshold_ratio: Math.round(ratio * 100) / 100 }),
      });
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
          <button type="button" role="switch" aria-checked={on} aria-label="自动压缩上下文" className={`switch ${on ? "on" : ""}`} onClick={() => setOn(!on)}>
            <span className="knob" />
          </button>
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
        {dirty ? <button className="link" onClick={() => { setOn(saved.auto_compact); setRatio(saved.threshold_ratio); }}>还原</button> : null}
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
        <button type="button" role="switch" aria-checked={!!on} aria-label="为资料库文件生成摘要" className={`switch ${on ? "on" : ""}`} disabled={on === null} onClick={toggle}>
          <span className="knob" />
        </button>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
    </div>
  );
}
