"use client";

import { useEffect, useRef, useState } from "react";
import { api, API_BASE, Brand, BrandAsset, BrandKind, errorMessage } from "../../lib/api";
import { formatBytes } from "../../lib/format";
import { IconImage, IconSheet, IconSlides, IconTrash, IconUpload } from "../icons";

const POLL_MS = 2000;
const SLOTS: { kind: BrandKind; label: string; accept: string; Icon: (p: { size?: number }) => React.ReactElement }[] = [
  { kind: "logo", label: "Logo", accept: "image/png,image/jpeg,image/svg+xml", Icon: IconImage },
  { kind: "pptx", label: "PPT 模板", accept: ".pptx,.potx", Icon: IconSlides },
  { kind: "docx", label: "Word 模板", accept: ".docx,.dotx", Icon: IconSheet },
];
const HEX = /^#[0-9a-fA-F]{6}$/;

/** What the template brings, read from the file itself: its colours, fonts and layouts. */
function Analysis({ asset }: { asset: BrandAsset }) {
  const a = asset.analysis || {};
  const theme = Array.isArray(a.themes) ? a.themes[0] || {} : {};
  // theme colours come as bare hex ("1F4E79") keyed by role (dk1, accent1, …)
  const colors: string[] = [...new Set(Object.values(theme.colors || {}).map((c) => `#${String(c)}`.toUpperCase()))].filter((c) => HEX.test(c)).slice(0, 8);
  const fonts: string[] = [theme.fonts?.majorFont, theme.fonts?.minorFont].filter((f) => typeof f === "string" && f);
  const layouts: number | null = Array.isArray(a.layouts) ? a.layouts.length : null;
  const styles: number | null = Array.isArray(a.styles) ? a.styles.length : null;
  return (
    <span className="brand-analysis">
      {colors.length ? (
        <span className="brand-swatches" aria-label="主题色">
          {colors.map((c) => <span key={c} style={{ background: c }} title={c} />)}
        </span>
      ) : null}
      {fonts.length ? <span>{[...new Set(fonts)].join(" / ")}</span> : null}
      {layouts ? <span>{layouts} 种版式</span> : null}
      {styles ? <span>{styles} 种样式</span> : null}
    </span>
  );
}

function Slot({ kind, label, accept, Icon, asset, onChange }: (typeof SLOTS)[number] & { asset: BrandAsset | null | undefined; onChange: () => void }) {
  const picker = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function upload(file: File) {
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/v1/admin/brand/assets/${kind}`, { method: "POST", body: form });
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      if (picker.current) picker.current.value = "";
    }
  }

  async function remove() {
    if (!confirm(`删除${label}？`)) return;
    setBusy(true);
    try {
      await api(`/v1/admin/brand/assets/${kind}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const state = asset?.status === "pending" ? "检查中" : asset?.status === "rejected" || asset?.status === "failed" ? "不可用" : "";
  return (
    <div className={`brand-slot ${asset ? "" : "empty"}`}>
      <input ref={picker} type="file" accept={accept} hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      {asset ? (
        <>
          <span className="brand-thumb">
            {asset.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={asset.thumbnail_url.startsWith("/api/") ? asset.thumbnail_url : `${API_BASE}${asset.thumbnail_url}`} alt="" />
            ) : (
              <Icon size={22} />
            )}
          </span>
          <span className="grow brand-slot-text">
            <span className="brand-slot-name">
              <strong>{label}</strong>
              <span className="hint" title={asset.filename}>{asset.filename}</span>
            </span>
            <span className="brand-slot-meta">
              {state ? <span className={`status ${state === "检查中" ? "queued" : "failed"}`}>{state}</span> : <Analysis asset={asset} />}
              <span className="hint">{formatBytes(asset.size)}</span>
            </span>
            {asset.analysis?.error ? <span className="alert error">{String(asset.analysis.error)}</span> : null}
          </span>
          <button className="icon-btn" onClick={() => picker.current?.click()} disabled={busy} aria-label={`替换${label}`} title="替换">
            <IconUpload size={16} />
          </button>
          <button className="icon-btn danger-hover" onClick={remove} disabled={busy} aria-label={`删除${label}`} title="删除">
            <IconTrash size={16} />
          </button>
        </>
      ) : (
        <button className="brand-upload" onClick={() => picker.current?.click()} disabled={busy}>
          {busy ? <span className="chip-spin" aria-hidden="true" /> : <Icon size={20} />}
          <span>上传{label}</span>
        </button>
      )}
      {error ? <span className="alert error">{error}</span> : null}
    </div>
  );
}

/** The company's look for generated files: colours, font, logo, and the PPT / Word templates work starts from. */
export default function BrandSection() {
  const [brand, setBrand] = useState<Brand | null>(null);
  const [draft, setDraft] = useState<{ enabled: boolean; name: string; primary: string; accent: string; font: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const request = useRef<AbortController | null>(null);

  function load() {
    request.current?.abort();
    const c = new AbortController();
    request.current = c;
    return api<Brand>("/v1/admin/brand", { signal: c.signal })
      .then((b) => {
        if (c.signal.aborted) return;
        setBrand(b);
        setDraft((d) => d || { enabled: b.enabled, name: b.name || "", primary: b.colors?.primary || "#1F4E79", accent: b.colors?.accent || "#E0A526", font: b.font || "" });
      })
      .catch((err) => !c.signal.aborted && setError(errorMessage(err)));
  }

  useEffect(() => {
    load();
    return () => request.current?.abort();
  }, []);

  // a template being checked settles on its own
  useEffect(() => {
    if (!brand || !Object.values(brand.assets || {}).some((a) => a?.status === "pending")) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [brand]);

  if (!brand || !draft) return <section className="admin-section">{error ? <div className="alert error">{error}</div> : <div className="pr-loading"><span /><span /><span /></div>}</section>;

  const valid = HEX.test(draft.primary) && HEX.test(draft.accent);
  const dirty =
    draft.enabled !== brand.enabled ||
    draft.name !== (brand.name || "") ||
    draft.primary.toLowerCase() !== (brand.colors?.primary || "").toLowerCase() ||
    draft.accent.toLowerCase() !== (brand.colors?.accent || "").toLowerCase() ||
    draft.font !== (brand.font || "");
  const set = (patch: Partial<typeof draft>) => setDraft({ ...draft, ...patch });

  async function save() {
    setBusy(true);
    setError("");
    try {
      const next = await api<Brand>("/v1/admin/brand", {
        method: "PATCH",
        body: JSON.stringify({ enabled: draft!.enabled, name: draft!.name.trim(), colors: { primary: draft!.primary, accent: draft!.accent }, font: draft!.font.trim() || null }),
      });
      setBrand(next);
      setDone(true);
      setTimeout(() => setDone(false), 1600);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const color = (key: "primary" | "accent", label: string) => (
    <label className="brand-color">
      <span>{label}</span>
      <span className="brand-color-input">
        <input type="color" value={HEX.test(draft[key]) ? draft[key] : "#000000"} onChange={(e) => set({ [key]: e.target.value.toUpperCase() })} aria-label={label} />
        <input type="text" value={draft[key]} maxLength={7} onChange={(e) => set({ [key]: e.target.value.trim() })} aria-label={`${label}色值`} className={HEX.test(draft[key]) ? "" : "invalid"} />
      </span>
    </label>
  );

  return (
    <section className="admin-section brand-settings">
      <div className="card card-pad">
        <div className="field-row">
          <span>生成文件使用公司品牌</span>
          <button type="button" role="switch" aria-checked={draft.enabled} aria-label="生成文件使用公司品牌" className={`switch ${draft.enabled ? "on" : ""}`} onClick={() => set({ enabled: !draft.enabled })}>
            <span className="knob" />
          </button>
        </div>
        <div className="brand-fields">
          <label className="field">
            <span>公司名称</span>
            <input type="text" value={draft.name} maxLength={60} onChange={(e) => set({ name: e.target.value })} />
          </label>
          <label className="field">
            <span>字体</span>
            <input type="text" value={draft.font} maxLength={60} onChange={(e) => set({ font: e.target.value })} placeholder="思源黑体" />
          </label>
          {color("primary", "主色")}
          {color("accent", "强调色")}
        </div>
        <div className="brand-preview" style={{ "--brand-primary": valid ? draft.primary : undefined, "--brand-accent": valid ? draft.accent : undefined } as React.CSSProperties} aria-hidden="true">
          <span className="brand-preview-bar" />
          <span className="brand-preview-title" style={{ fontFamily: draft.font || undefined }}>{draft.name || "标题写结论"}</span>
          <span className="brand-preview-line" />
          <span className="brand-preview-line short" />
          <span className="brand-preview-dot" />
        </div>
      </div>
      <div className="context-foot">
        <button onClick={save} disabled={!dirty || !valid || busy}>{busy ? "保存中…" : done ? "已保存" : "保存"}</button>
        {dirty ? (
          <button className="link" onClick={() => setDraft({ enabled: brand.enabled, name: brand.name || "", primary: brand.colors?.primary || "#1F4E79", accent: brand.colors?.accent || "#E0A526", font: brand.font || "" })}>
            还原
          </button>
        ) : null}
        {error ? <span className="alert error">{error}</span> : null}
      </div>
      <div className="card brand-slots">
        {SLOTS.map((slot) => (
          <Slot key={slot.kind} {...slot} asset={brand.assets?.[slot.kind]} onChange={load} />
        ))}
      </div>
    </section>
  );
}
