"use client";

import { useEffect, useRef, useState } from "react";
import { api, API_BASE, errorMessage, FileItem, Template, TemplateList, TemplateRef } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { usePopover } from "../../lib/use-popover";
import { IconCheck, IconChevronDown, IconLibrary, IconSlides, IconTemplate, IconUpload, IconX } from "../icons";
import LibraryPicker from "../library/library-picker";

const POLL_MS = 1500;

/** The template a message follows: a saved one, or a library PPT for this message only. */
export type TemplateChoice = { ref: TemplateRef; name: string; pending: boolean };

const stem = (filename: string) => filename.replace(/\.(pptx|potx)$/i, "");
const refId = (ref: TemplateRef) => ("id" in ref ? ref.id : ref.file_id);

function Thumb({ t }: { t: Template }) {
  if (t.status === "pending") return <span className="chip-spin" aria-label="检查中" />;
  if (t.status !== "ready") return <IconX size={18} />;
  // eslint-disable-next-line @next/next/no-img-element
  return t.thumbnail_url ? <img src={`${API_BASE}${t.thumbnail_url}`} alt="" loading="lazy" /> : <IconSlides size={20} />;
}

/** "模板" next to the PPT skill: none by default; the company's, the user's own, or any PPT from the library. */
export default function TemplatePicker({
  value,
  onChange,
  projectId,
}: {
  value: TemplateChoice | null;
  onChange: (next: TemplateChoice | null) => void;
  projectId?: string | null;
}) {
  const { open, setOpen, box } = usePopover();
  const [list, setList] = useState<TemplateList | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [library, setLibrary] = useState(false);
  const picker = useRef<HTMLInputElement | null>(null);
  const latest = useRef(value);
  latest.current = value;

  useEffect(() => {
    if (!open) return;
    setError("");
    const c = new AbortController();
    api<TemplateList>("/v1/templates?kind=pptx", { signal: c.signal })
      .then((l) => !c.signal.aborted && setList(l))
      .catch((err) => !c.signal.aborted && setError(errorMessage(err)));
    return () => c.abort();
  }, [open]);

  // a template still being checked: follow it until it can be used (or can't)
  const pendingIds = [...(list?.personal || []), ...(list?.company || [])].filter((t) => t.status === "pending").map((t) => t.id);
  if (value?.pending && "id" in value.ref && !pendingIds.includes(value.ref.id)) pendingIds.push(value.ref.id);
  const pendingKey = pendingIds.join(",");
  useEffect(() => {
    if (!pendingKey) return;
    let stale = false;
    const timer = setTimeout(async () => {
      const fresh = await Promise.all(pendingKey.split(",").map((id) => api<Template>(`/v1/templates/${id}`).catch(() => null)));
      if (stale) return;
      const byId = new Map(fresh.filter((t): t is Template => !!t).map((t) => [t.id, t]));
      setList((l) => (l ? { company: l.company.map((t) => byId.get(t.id) || t), personal: l.personal.map((t) => byId.get(t.id) || t) } : l));
      const chosen = latest.current;
      const now = chosen && "id" in chosen.ref ? byId.get(chosen.ref.id) : undefined;
      if (chosen?.pending && now && now.status !== "pending") {
        if (now.status === "ready") onChange({ ...chosen, pending: false });
        else {
          onChange(null);
          setError(now.reason || "这份模板无法使用");
        }
      }
    }, POLL_MS);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingKey, list]);

  function choose(t: Template) {
    if (t.status !== "ready" && t.status !== "pending") return;
    onChange({ ref: { id: t.id }, name: t.name, pending: t.status === "pending" });
    setOpen(false);
  }

  async function upload(file: File) {
    setUploading(true);
    setError("");
    const form = new FormData();
    form.append("upload", file);
    form.append("name", stem(file.name));
    try {
      const t = await api<Template>("/v1/templates", { method: "POST", body: form });
      setList((l) => ({ company: l?.company || [], personal: [t, ...(l?.personal || [])] }));
      onChange({ ref: { id: t.id }, name: t.name, pending: t.status === "pending" });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
      if (picker.current) picker.current.value = "";
    }
  }

  async function remove(t: Template) {
    if (!(await confirmDialog({ title: `删除模板「${t.name}」？`, action: "删除", danger: true }))) return;
    try {
      await api(`/v1/templates/${t.id}`, { method: "DELETE" });
      setList((l) => (l ? { ...l, personal: l.personal.filter((x) => x.id !== t.id) } : l));
      if (value && "id" in value.ref && value.ref.id === t.id) onChange(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const selected = value ? refId(value.ref) : null;
  const card = (t: Template) => (
    <div key={t.id} className={`tpl-item ${selected === t.id ? "on" : ""} ${t.status === "ready" || t.status === "pending" ? "" : "bad"}`}>
      <button className="tpl-card" onClick={() => choose(t)} aria-pressed={selected === t.id} title={t.status === "failed" || t.status === "rejected" ? t.reason || "无法使用" : t.name}>
        <span className="tpl-thumb">
          <Thumb t={t} />
          {selected === t.id ? <span className="tpl-check"><IconCheck size={12} /></span> : null}
        </span>
        <span className="tpl-name">{t.name}</span>
      </button>
      {t.can_edit && t.scope === "personal" ? (
        <button className="tpl-del" aria-label={`删除模板 ${t.name}`} title="删除" onClick={() => remove(t)}>
          <IconX size={12} />
        </button>
      ) : null}
    </div>
  );

  return (
    <div className="tpl-anchor" ref={box}>
      <button className={`tpl-chip ${value ? "on" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog">
        {value?.pending ? <span className="chip-spin" aria-hidden="true" /> : <IconTemplate size={14} />}
        <span className="tpl-chip-label">{value ? value.name : "模板"}</span>
        <IconChevronDown size={12} />
      </button>
      {open ? (
        <div className="pop-menu tpl-panel" role="dialog" aria-label="选择模板">
          <div className="tpl-scroll">
            <div className="tpl-grid">
              <div className={`tpl-item none ${value ? "" : "on"}`}>
                <button className="tpl-card" onClick={() => (onChange(null), setOpen(false))} aria-pressed={!value}>
                  <span className="tpl-thumb">
                    <IconSlides size={20} />
                    {!value ? <span className="tpl-check"><IconCheck size={12} /></span> : null}
                  </span>
                  <span className="tpl-name">不用模板</span>
                </button>
              </div>
              {value && "file_id" in value.ref ? (
                <div className="tpl-item on">
                  <button className="tpl-card" onClick={() => setOpen(false)} aria-pressed title={value.name}>
                    <span className="tpl-thumb">
                      <IconLibrary size={20} />
                      <span className="tpl-check"><IconCheck size={12} /></span>
                    </span>
                    <span className="tpl-name">{value.name}</span>
                  </button>
                </div>
              ) : null}
              {list?.company.map(card)}
            </div>
            {list?.personal.length ? (
              <>
                <div className="tpl-group">我的</div>
                <div className="tpl-grid">{list.personal.map(card)}</div>
              </>
            ) : null}
            {!list && !error ? <div className="pr-loading"><span /><span /><span /></div> : null}
          </div>
          {error ? <div className="alert error">{error}</div> : null}
          <div className="tpl-foot">
            <button className="menu-item" onClick={() => picker.current?.click()} disabled={uploading}>
              {uploading ? <span className="chip-spin" aria-hidden="true" /> : <IconUpload size={15} />} 上传模板
            </button>
            <button className="menu-item" onClick={() => setLibrary(true)}>
              <IconLibrary size={15} /> 从资料库选择
            </button>
          </div>
          <input ref={picker} type="file" accept=".pptx,.potx" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </div>
      ) : null}
      {library ? (
        <LibraryPicker
          only="slides"
          title="选择一份 PPT 作为模板"
          projectId={projectId}
          attached={[]}
          onClose={() => setLibrary(false)}
          onPick={(files: FileItem[]) => {
            const f = files[0];
            setLibrary(false);
            if (!f) return;
            onChange({ ref: { file_id: f.id }, name: stem(f.filename), pending: false });
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}
