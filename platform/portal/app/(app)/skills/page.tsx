"use client";

import { formatBytes } from "../../../lib/format";

import { useCallback, useEffect, useRef, useState } from "react";
import Switch from "../../../components/switch";
import { IconCopyPlus, IconDownload, IconEye, IconPencil, IconPlus, IconRetry, IconSparkles, IconTrash, IconUpload } from "../../../components/icons";
import RowMenu, { RowAction } from "../../../components/row-menu";
import { skillIcon } from "../../../components/chat/composer";
import SkillEditor, { EditorTarget } from "../../../components/skills/skill-editor";
import DraftEditor from "../../../components/skills/draft-editor";
import { isLocked, scanProblem, ScannedFile, waitForScan } from "../../../components/skills/skill-save";
import { API_BASE, api, downloadUrl, errorMessage, FileItem, redirectIfUnauthorized, Skill, SkillDraft, skillKind } from "../../../lib/api";
import { confirmDialog } from "../../../lib/dialog";
import { draftLabel, draftsChanged, useActiveDrafts, working } from "../../../lib/skill-drafts";

const TEMPLATE = `---
name: weekly-report
title: 周报助手
description: 按团队模板写周报：本周完成、问题与风险、下周计划
---

# 什么时候用
用户要写周报、整理一周的工作时。

# 怎么做
1. 先弄清楚本周做了什么：用户给的文字、上传的材料，不清楚就问。
2. 按下面的结构写，每部分 3～5 条，每条一句话，先写结果再写过程：
   - 本周完成
   - 问题与风险（写清楚需要谁帮忙）
   - 下周计划（带时间点）
3. 需要交付文件时，生成 Word 或 Markdown 放到 /workspace/outputs/。

# 可以附带脚本
把这个 SKILL.md 和 scripts/、assets/ 等文件夹一起打成 .zip 上传，
说明里用相对路径引用，比如：python scripts/fill.py
`;

async function getFile(id: string, signal: AbortSignal): Promise<ScannedFile | null> {
  const resp = await fetch(`${API_BASE}/v1/files/${id}`, { signal, cache: "no-store" });
  redirectIfUnauthorized(resp);
  return resp.ok ? resp.json() : null;
}

function SkillRow({
  s,
  busy,
  locked,
  onToggle,
  onOpen,
  onCustomize,
  onDelete,
}: {
  s: Skill;
  busy: boolean;
  locked?: boolean;
  onToggle: (on: boolean) => void;
  onOpen: () => void;
  onCustomize?: () => void;
  onDelete?: () => void;
}) {
  const Icon = skillIcon(s.name);
  const kind = skillKind(s);
  const personal = kind === "personal";
  const toggleable = s.toggleable ?? kind !== "base";
  const zip = personal && s.kind_file === "zip" && s.file_id;
  const actions: RowAction[] = personal
    ? [
        { label: "编辑", icon: <IconPencil size={15} />, onSelect: onOpen, disabled: locked },
        ...(zip ? [{ label: "下载", icon: <IconDownload size={15} />, onSelect: () => download(downloadUrl(`/v1/files/${s.file_id}/content`), s.filename) }] : []),
        ...(onDelete ? ["sep" as const, { label: "删除", icon: <IconTrash size={15} />, danger: true, onSelect: onDelete }] : []),
      ]
    : [
        { label: "查看", icon: <IconEye size={15} />, onSelect: onOpen },
        ...(onCustomize ? [{ label: "另存为个人技能", icon: <IconCopyPlus size={15} />, onSelect: onCustomize }] : []),
      ];
  return (
    <div className={`mrow ${s.enabled || !toggleable ? "" : "off"}`}>
      <span className={`mrow-icon ${s.error ? "broken" : ""}`}>
        <Icon size={17} />
      </span>
      <button className="mrow-main" onClick={onOpen} disabled={locked} aria-label={`${personal ? "编辑" : "查看"} ${s.title}`}>
        <span className="mrow-title">
          <span>{s.title}</span>
          {zip ? <span className="state">{s.files} 个文件</span> : null}
        </span>
        {s.error ? <span className="mrow-desc error">无法使用：{s.error}</span> : s.description ? <span className="mrow-desc">{s.description}</span> : null}
      </button>
      <div className="mrow-trail">
        <RowMenu label={s.title} actions={actions} />
        {toggleable ? (
          <Switch on={s.enabled} label={`${s.enabled ? "关闭" : "开启"} ${s.title}`} disabled={busy || !!s.error} onChange={onToggle} />
        ) : (
          <span className="switch-slot" />
        )}
      </div>
    </div>
  );
}

function download(href: string, filename?: string) {
  Object.assign(document.createElement("a"), { href, download: filename || "" }).click();
}

function DraftRow({ d, onOpen, onDiscard }: { d: SkillDraft; onOpen: () => void; onDiscard: () => void }) {
  const title = d.title || d.name || (d.base ? d.base.name : d.conversation_title ? `「${d.conversation_title}」` : "技能草稿");
  return (
    <div className="mrow">
      <span className="mrow-icon">{working(d) ? <span className="draft-spin" /> : <IconSparkles size={17} />}</span>
      <button className="mrow-main" onClick={onOpen} aria-label={`打开草稿 ${title}`}>
        <span className="mrow-title"><span>{title}</span></span>
        {d.status === "failed" && d.error ? <span className="mrow-desc error">{d.error}</span> : d.summary ? <span className="mrow-desc">{d.summary}</span> : null}
      </button>
      <div className="mrow-trail">
        <span className={`state ${d.status}`}>{draftLabel(d)}</span>
        <RowMenu
          label={title}
          actions={[
            ...(d.status === "failed" ? [{ label: "重试", icon: <IconRetry size={15} />, onSelect: onOpen }] : []),
            { label: "丢弃草稿", icon: <IconTrash size={15} />, danger: true, onSelect: onDiscard },
          ]}
        />
        <span className="switch-slot" />
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [storage, setStorage] = useState<{ used_bytes: number; limit_bytes: number; available_bytes: number } | null>(null);
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string[]>([]); // file names still being scanned
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<EditorTarget | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [scanning, setScanning] = useState<ReadonlySet<string>>(new Set()); // new versions not yet scanned
  const drafts = useActiveDrafts();
  const scans = useRef<AbortController | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const generation = useRef(0);
  const disposed = useRef(false);

  const load = useCallback(() => {
    const current = ++generation.current;
    const active = () => !disposed.current && generation.current === current;
    api<{ used_bytes: number; limit_bytes: number; available_bytes: number }>("/v1/skills/storage")
      .then((value) => { if (active()) setStorage(value); })
      .catch(() => undefined);
    return api<Skill[]>("/v1/skills")
      .then((value) => { if (active()) setSkills(value.filter((s) => !s.implicit)); })
      .catch((e) => { if (active()) setError(errorMessage(e)); });
  }, []);
  useEffect(() => {
    disposed.current = false;
    scans.current = new AbortController();
    load();
    // a link from a notification or another page: /skills?draft=sd_…
    const wanted = new URLSearchParams(window.location.search).get("draft");
    if (wanted) setDraft(wanted);
    return () => { disposed.current = true; generation.current++; scans.current?.abort(); };
  }, [load]);

  /** Wait for the Worker to scan a new file, then list it. Null when still pending at the deadline, undefined once the page is gone. */
  async function scanned(fileId: string): Promise<ScannedFile | null | undefined> {
    const signal = scans.current!.signal;
    setScanning((ids) => new Set(ids).add(fileId));
    try {
      return await waitForScan(fileId, getFile, { signal });
    } catch {
      return undefined;
    } finally {
      if (!signal.aborted) {
        setScanning((ids) => { const next = new Set(ids); next.delete(fileId); return next; });
        await load();
      }
    }
  }

  async function settle(fileId: string, label: string) {
    load(); // the new head shows up as being scanned, which also locks it against another save
    const file = await scanned(fileId);
    if (file === null) setError(`${label}：仍在安全扫描，稍后刷新查看`);
    else if (file && scanProblem(file)) setError(`${label}：${scanProblem(file)}`);
  }

  function saved(fileId: string, label: string) {
    setEditing(null);
    setDraft(null);
    setError("");
    settle(fileId, label);
  }

  /** Edit a personal skill, or start a personal copy of a platform one, as a draft. An open draft of the same skill is reused. */
  async function openDraft(s: Skill) {
    setEditing(null);
    setError("");
    const kind = skillKind(s);
    const existing = drafts?.find((d) => d.origin === "skill" && d.base?.name === s.name && (kind !== "personal" || d.base?.file_id === s.file_id));
    if (existing) return setDraft(existing.id);
    setBusy(s.name);
    try {
      const created = await api<SkillDraft>(`/v1/skills/${encodeURIComponent(s.name)}/drafts`, { method: "POST", body: "{}" });
      draftsChanged();
      setDraft(created.id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function discard(d: SkillDraft) {
    if (!(await confirmDialog({ title: "丢弃这个草稿？", body: "已有的技能不受影响。", action: "丢弃", danger: true }))) return;
    try {
      await api(`/v1/skill-drafts/${d.id}?expected_revision=${d.revision}`, { method: "DELETE" });
    } catch (e) {
      setError(errorMessage(e));
    }
    draftsChanged();
  }

  async function toggle(s: Skill, enabled: boolean) {
    setBusy(s.name);
    setError("");
    setSkills((list) => list?.map((x) => (x.name === s.name && x.scope === s.scope ? { ...x, enabled } : x)) || list); // optimistic
    try {
      await api(`/v1/skills/${s.name}`, { method: "PUT", body: JSON.stringify({ enabled }) });
    } catch (e) {
      setError(`没能保存：${errorMessage(e)}`);
      load();
    } finally {
      setBusy(null);
    }
  }

  async function upload(list: FileList) {
    setError("");
    for (const file of Array.from(list)) {
      if (disposed.current) return;
      const form = new FormData();
      form.append("upload", file);
      const resp = await fetch(`${API_BASE}/v1/files?purpose=skill`, { method: "POST", body: form });
      redirectIfUnauthorized(resp);
      if (!resp.ok) {
        setError(`${file.name}：上传失败（${resp.status}）`);
        continue;
      }
      const item: FileItem = await resp.json();
      setUploading((u) => [...u, file.name]);
      // the Worker scans the file and recognises the skill; wait for it
      const f = await scanned(item.id);
      if (f === undefined) return;
      setUploading((u) => u.filter((n) => n !== file.name));
      if (f === null) setError(`${file.name}：仍在安全扫描，稍后刷新查看`);
      else if (f.status === "rejected" || (!f.meta?.skill && !f.meta?.skill_error)) setError(`${file.name}：${scanProblem(f)}`);
    }
    if (picker.current) picker.current.value = "";
  }

  async function remove(s: Skill) {
    if (!s.file_id || !(await confirmDialog({ title: `删除技能「${s.title}」？`, body: "技能文件将永久删除。", action: "删除", danger: true }))) return;
    const resp = await fetch(`${API_BASE}/v1/skills/files/${s.file_id}`, { method: "DELETE" });
    redirectIfUnauthorized(resp);
    if (!resp.ok) setError(`删除失败（${resp.status}）`);
    load();
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATE], { type: "text/markdown;charset=utf-8" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "SKILL.md" });
    a.click();
    URL.revokeObjectURL(url);
  }

  const platform = (skills || []).filter((s) => skillKind(s) !== "personal");
  const personal = (skills || []).filter((s) => skillKind(s) === "personal");
  const openDrafts = (drafts || []).filter((d) => d.status !== "confirmed" && d.status !== "discarded");

  return (
    <main className="page mpage">
      <div className="mpage-head">
        <h1>技能</h1>
        <button className="secondary sm" onClick={() => picker.current?.click()}>
          <IconUpload size={15} /> 上传
        </button>
        <button className="sm" onClick={() => setEditing({ mode: "create", content: TEMPLATE })}>
          <IconPlus size={15} /> 新建
        </button>
        <input ref={picker} type="file" accept=".md,.zip" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
      </div>
      {error ? <div className="alert error" style={{ marginBottom: 12 }}>{error}</div> : null}

      {openDrafts.length ? (
        <section className="msection" aria-label="技能草稿">
          <div className="msection-head"><h2>草稿</h2></div>
          <div className="mlist">
            {openDrafts.map((d) => <DraftRow key={d.id} d={d} onOpen={() => setDraft(d.id)} onDiscard={() => discard(d)} />)}
          </div>
        </section>
      ) : null}

      <section className="msection">
        <div className="msection-head">
          <h2>个人技能</h2>
          {storage ? <span className="meta">已用 {formatBytes(storage.used_bytes)} · 剩余 {formatBytes(storage.available_bytes)}</span> : null}
          <div className="actions">
            <button className="link sm" onClick={downloadTemplate}>
              <IconDownload size={14} /> 模板
            </button>
          </div>
        </div>
        <div className="mlist">
          {uploading.map((n) => (
            <div key={n} className="mrow off">
              <span className="mrow-icon"><span className="draft-spin" /></span>
              <div className="mrow-main"><span className="mrow-desc">正在上传并识别 {n}…</span></div>
            </div>
          ))}
          {skills === null ? (
            <div className="mlist-empty">加载中…</div>
          ) : personal.length === 0 && uploading.length === 0 ? (
            <div className="mlist-empty">上传 SKILL.md 或 ZIP，或在对话里提取技能</div>
          ) : null}
          {personal.map((s) => (
            <SkillRow
              key={s.file_id}
              s={s}
              busy={busy === s.name}
              locked={isLocked(s, scanning) || busy === s.name}
              onToggle={(on) => toggle(s, on)}
              onOpen={() => openDraft(s)}
              onDelete={() => remove(s)}
            />
          ))}
        </div>
      </section>

      <section className="msection">
        <div className="msection-head"><h2>平台技能</h2></div>
        <div className="mlist">
          {skills === null ? (
            <div className="mlist-empty">加载中…</div>
          ) : (
            platform.map((s) => (
              <SkillRow
                key={s.name}
                s={s}
                busy={busy === s.name}
                onToggle={(on) => toggle(s, on)}
                onOpen={() => setEditing({ mode: "view", skill: s })}
                onCustomize={() => openDraft(s)}
              />
            ))
          )}
        </div>
      </section>
      {editing ? (
        <SkillEditor target={editing} onClose={() => setEditing(null)} onSaved={saved} onCustomize={(s) => openDraft(s)} />
      ) : null}
      {draft ? <DraftEditor key={draft} draftId={draft} onClose={() => setDraft(null)} onConfirmed={saved} /> : null}
    </main>
  );
}
