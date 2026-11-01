"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MemoryCard } from "../../../components/memory/memory-summary";
import Switch from "../../../components/switch";
import { api, errorMessage } from "../../../lib/api";

type Settings = { enabled: boolean; auto_save: boolean; reference_history: boolean };
type DreamSettings = { enabled: boolean; timezone: string | null; window_start: string; window_end: string };
type Doc = Settings & { dream: DreamSettings };

const SWITCHES: { key: keyof Settings; label: string }[] = [
  { key: "enabled", label: "使用记忆" },
  { key: "auto_save", label: "自动记忆" },
  { key: "reference_history", label: "引用历史对话" },
];

/** What the AI remembers, as one summary, and the switches that decide whether it remembers at all. */
export default function MemoryPage() {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const loads = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    loads.current?.abort();
    const controller = new AbortController();
    loads.current = controller;
    api<Doc>("/v1/memory", { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setDoc({ enabled: value.enabled, auto_save: value.auto_save, reference_history: value.reference_history !== false, dream: value.dream });
        setError("");
      })
      .catch((e) => !controller.signal.aborted && setError(errorMessage(e)));
  }, []);

  useEffect(() => {
    load();
    return () => loads.current?.abort();
  }, [load]);

  async function set(change: Partial<Settings>) {
    if (!doc || saving) return;
    loads.current?.abort(); // an older read must not undo the switch
    setSaving(true);
    setError("");
    setDoc({ ...doc, ...change });
    try {
      const saved = await api<Settings>("/v1/memory/settings", { method: "PATCH", body: JSON.stringify(change) });
      setDoc((d) => (d ? { ...d, enabled: saved.enabled, auto_save: saved.auto_save, reference_history: saved.reference_history } : d));
    } catch (e) {
      setError(errorMessage(e));
      load();
    } finally {
      setSaving(false);
    }
  }

  async function setDream(enabled: boolean) {
    if (!doc || saving) return;
    setSaving(true);
    setError("");
    setDoc({ ...doc, dream: { ...doc.dream, enabled } });
    try {
      const dream = await api<DreamSettings>("/v1/memory/dream", { method: "PATCH", body: JSON.stringify({ ...doc.dream, enabled }) });
      setDoc((d) => (d ? { ...d, dream } : d));
    } catch (e) {
      setError(errorMessage(e));
      load();
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="page mpage memory-page">
      <div className="mpage-head">
        <h1>记忆</h1>
      </div>
      {error ? <div className="alert error memory-alert" role="alert">{error}</div> : null}
      {doc === null ? (
        error ? null : <div className="mlist"><div className="mlist-empty">加载中…</div></div>
      ) : (
        <>
          <section className="msection">
            <div className="mlist">
              <MemoryCard projectId={null} title="你的记忆" disabled={!doc.enabled} />
            </div>
          </section>
          <section className="msection">
            <div className="mlist">
              {SWITCHES.map(({ key, label }) => (
                <div key={key} className={`mrow ${key !== "enabled" && !doc.enabled ? "off" : ""}`}>
                  <div className="mrow-main"><span className="mrow-title"><span>{label}</span></span></div>
                  <div className="mrow-trail">
                    <Switch on={doc[key]} label={label} disabled={saving || (key !== "enabled" && !doc.enabled)} onChange={(on) => set({ [key]: on })} />
                  </div>
                </div>
              ))}
              <div className={`mrow ${doc.enabled ? "" : "off"}`}>
                <div className="mrow-main"><span className="mrow-title"><span>夜间自动整理</span></span></div>
                <div className="mrow-trail">
                  <Switch on={doc.dream.enabled} label="夜间自动整理" disabled={saving || !doc.enabled} onChange={setDream} />
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
