"use client";

import { useEffect, useRef, useState } from "react";
import { api, API_BASE, Brand, BrandAsset, BrandKind, errorMessage, Template, TemplateList } from "../../lib/api";
import { formatBytes } from "../../lib/format";
import { IconImage, IconPencil, IconSheet, IconSlides, IconTrash, IconUpload } from "../icons";
import { confirmDialog } from "../../lib/dialog";
import { usePopover } from "../../lib/use-popover";

const POLL_MS = 2000;
const SLOTS: { kind: BrandKind; label: string; accept: string; Icon: (p: { size?: number }) => React.ReactElement }[] = [
  { kind: "logo", label: "Logo", accept: "image/png,image/jpeg,image/svg+xml", Icon: IconImage },
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
    if (!(await confirmDialog({ title: `删除${label}？`, action: "删除", danger: true }))) return;
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

// a palette of common corporate colours; the hex field beside it takes any other
const PALETTE = [
  "#1F4E79", "#2563EB", "#0EA5E9", "#0F766E", "#16A34A", "#65A30D",
  "#CA8A04", "#E0A526", "#EA580C", "#DC2626", "#BE185D", "#7C3AED",
  "#4338CA", "#334155", "#111827", "#6B7280",
];

/** The colour as a swatch; a click opens the palette in the page (not the system colour panel). */
function ColorSwatch({ value, onChange, label }: { value: string; onChange: (hex: string) => void; label: string }) {
  const { open, setOpen, box } = usePopover();
  return (
    <span className="color-swatch" ref={box}>
      <button type="button" className="color-swatch-btn" style={{ background: HEX.test(value) ? value : undefined }} aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)} />
      {open ? (
        <div className="pop-menu color-palette" role="listbox" aria-label={label}>
          {PALETTE.map((c) => (
            <button
              key={c}
              type="button"
              role="option"
              aria-selected={c.toLowerCase() === value.toLowerCase()}
              aria-label={c}
              title={c}
              style={{ background: c }}
              onClick={() => {
                onChange(c);
                setOpen(false);
              }}
            />
          ))}
        </div>
      ) : null}
    </span>
  );
}

/** One company template: its first slide, its name (click the pencil to rename), what it brings. */
function TemplateRow({ t, onChanged, onRemoved }: { t: Template; onChanged: (t: Template) => void; onRemoved: () => void }) {
  const [name, setName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function rename() {
    const value = name?.trim();
    setName(null);
    if (!value || value === t.name) return;
    try {
      onChanged(await api<Template>(`/v1/templates/${t.id}`, { method: "PATCH", body: JSON.stringify({ name: value }) }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove() {
    if (!(await confirmDialog({ title: `删除模板「${t.name}」？`, body: "同事将不能再选用它。", action: "删除", danger: true }))) return;
    setBusy(true);
    try {
      await api(`/v1/templates/${t.id}`, { method: "DELETE" });
      onRemoved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const state = t.status === "pending" ? "检查中" : t.status === "ready" ? "" : t.reason || "不可用";
  return (
    <div className="brand-slot">
      <span className="brand-thumb wide">
        {t.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`${API_BASE}${t.thumbnail_url}`} alt="" />
        ) : t.status === "pending" ? (
          <span className="chip-spin" aria-hidden="true" />
        ) : (
          <IconSlides size={20} />
        )}
      </span>
      <span className="grow brand-slot-text">
        {name !== null ? (
          <input
            className="tpl-rename"
            autoFocus
            value={name}
            maxLength={60}
            aria-label="模板名称"
            onChange={(e) => setName(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) e.currentTarget.blur();
              if (e.key === "Escape") setName(null);
            }}
          />
        ) : (
          <span className="brand-slot-name">
            <strong className="ellipsis" title={t.filename}>{t.name}</strong>
          </span>
        )}
        <span className="brand-slot-meta">
          {state ? <span className={`status ${t.status === "pending" ? "queued" : "failed"}`}>{state}</span> : null}
          {t.layouts ? <span>{t.layouts} 种版式</span> : null}
          <span className="hint">{formatBytes(t.size)}</span>
        </span>
        {error ? <span className="alert error">{error}</span> : null}
      </span>
      <button className="icon-btn" onClick={() => setName(t.name)} disabled={busy} aria-label={`重命名 ${t.name}`} title="重命名">
        <IconPencil size={16} />
      </button>
      <button className="icon-btn danger-hover" onClick={remove} disabled={busy} aria-label={`删除 ${t.name}`} title="删除">
        <IconTrash size={16} />
      </button>
    </div>
  );
}

/** The PPT templates everyone can pick from. Several are fine: a report deck, a pitch deck, … */
function CompanyTemplates() {
  const [list, setList] = useState<Template[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const picker = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const c = new AbortController();
    api<TemplateList>("/v1/templates?kind=pptx", { signal: c.signal })
      .then((l) => !c.signal.aborted && setList(l.company))
      .catch((err) => !c.signal.aborted && setError(errorMessage(err)));
    return () => c.abort();
  }, []);

  const pendingKey = (list || []).filter((t) => t.status === "pending").map((t) => t.id).join(",");
  useEffect(() => {
    if (!pendingKey) return;
    let stale = false;
    const timer = setTimeout(async () => {
      const fresh = await Promise.all(pendingKey.split(",").map((id) => api<Template>(`/v1/templates/${id}`).catch(() => null)));
      if (stale) return;
      const byId = new Map(fresh.filter((t): t is Template => !!t).map((t) => [t.id, t]));
      setList((l) => (l ? l.map((t) => byId.get(t.id) || t) : l));
    }, POLL_MS);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [pendingKey, list]);

  async function upload(files: FileList) {
    setUploading(true);
    setError("");
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("upload", file);
      form.append("name", file.name.replace(/\.(pptx|potx)$/i, ""));
      form.append("scope", "company");
      try {
        const t = await api<Template>("/v1/templates", { method: "POST", body: form });
        setList((l) => [t, ...(l || [])]);
      } catch (err) {
        setError(errorMessage(err));
      }
    }
    setUploading(false);
    if (picker.current) picker.current.value = "";
  }

  return (
    <div className="brand-templates">
      <div className="brand-templates-head">
        <h3>PPT 模板</h3>
        <button className="secondary sm" onClick={() => picker.current?.click()} disabled={uploading}>
          {uploading ? <span className="chip-spin" aria-hidden="true" /> : <IconUpload size={15} />} 上传
        </button>
        <input ref={picker} type="file" accept=".pptx,.potx" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {list === null ? (
        error ? null : <div className="pr-loading"><span /><span /><span /></div>
      ) : list.length ? (
        <div className="card brand-slots">
          {list.map((t) => (
            <TemplateRow
              key={t.id}
              t={t}
              onChanged={(next) => setList((l) => (l || []).map((x) => (x.id === next.id ? next : x)))}
              onRemoved={() => setList((l) => (l || []).filter((x) => x.id !== t.id))}
            />
          ))}
        </div>
      ) : (
        <button className="brand-upload" onClick={() => picker.current?.click()} disabled={uploading}>
          <IconSlides size={20} />
          <span>上传公司 PPT 模板</span>
        </button>
      )}
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
        body: JSON.stringify({ enabled: draft!.enabled, name: draft!.name.trim(), colors: { primary: draft!.primary, accent: draft!.accent }, font: draft!.font.trim() }),
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
        <ColorSwatch value={draft[key]} onChange={(c) => set({ [key]: c })} label={label} />
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
      <CompanyTemplates />
    </section>
  );
}
