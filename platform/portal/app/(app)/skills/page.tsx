"use client";

import { formatBytes } from "../../../lib/format";

import { useCallback, useEffect, useRef, useState } from "react";
import Switch from "../../../components/switch";
import { IconChart, IconDownload, IconPlus, IconSheet, IconSlides, IconSparkles, IconUpload } from "../../../components/icons";
import SkillEditor, { EditorTarget } from "../../../components/skills/skill-editor";
import MarketPanel from "../../../components/skills/market";
import { isLocked, scanProblem, ScannedFile, waitForScan } from "../../../components/skills/skill-save";
import { API_BASE, api, FileItem, redirectIfUnauthorized, Skill } from "../../../lib/api";
import { confirmDialog } from "../../../lib/dialog";

const ICONS: Record<string, (p: { size?: number }) => React.ReactElement> = { pptx: IconSlides, xlsx: IconSheet, data: IconChart };

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
  onDelete,
}: {
  s: Skill;
  busy: boolean;
  locked?: boolean;
  onToggle: (on: boolean) => void;
  onOpen: () => void;
  onDelete?: () => void;
}) {
  const Icon = ICONS[s.name] || IconSparkles;
  const action = s.scope === "personal" && s.kind === "zip" ? "更新" : s.scope === "personal" && s.kind === "md" ? "编辑" : "查看";
  return (
    <div className={`skill-row ${s.enabled ? "" : "off"} ${s.error ? "broken" : ""}`}>
      <span className="skill-icon">
        <Icon size={20} />
      </span>
      <div className="grow">
        <div className="skill-title">
          {s.title}
          {s.name ? <span className="chip mono">{s.name}</span> : null}
          {s.scope === "personal" ? <span className="chip">{s.kind === "zip" ? `压缩包 · ${s.files} 个文件` : "SKILL.md"}</span> : <span className="chip">内置</span>}
        </div>
        {s.description ? <div className="skill-desc">{s.description}</div> : null}
        {s.scope === "personal" ? (
          <div className="skill-file hint">
            技能文件：{s.filename}
            {s.error ? <span className="error">　无法使用：{s.error}</span> : null}
          </div>
        ) : null}
      </div>
      <div className="skill-editor-actions">
        <button className="link" onClick={onOpen} disabled={locked} aria-label={`${action} ${s.title}`}>
          {action}
        </button>
        {onDelete ? (
          <button className="link danger-text" onClick={onDelete} title="删除这个技能">
            删除
          </button>
        ) : null}
      </div>
      <Switch on={s.enabled} label={`${s.enabled ? "关闭" : "开启"} ${s.title}`} disabled={busy || !!s.error} onChange={onToggle} />
    </div>
  );
}

export default function SkillsPage() {
  const [storage, setStorage] = useState<{used_bytes: number; limit_bytes: number; available_bytes: number} | null>(null);
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string[]>([]); // file names still being scanned
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"mine" | "market">("mine");
  const [editing, setEditing] = useState<EditorTarget | null>(null);
  const [scanning, setScanning] = useState<ReadonlySet<string>>(new Set()); // new versions not yet scanned
  const scans = useRef<AbortController | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const generation = useRef(0);
  const disposed = useRef(false);

  const load = useCallback(() => {
    const current = ++generation.current;
    const active = () => !disposed.current && generation.current === current;
    api<{used_bytes: number; limit_bytes: number; available_bytes: number}>("/v1/skills/storage")
      .then((value) => { if (active()) setStorage(value); }).catch(() => undefined);
    return api<Skill[]>("/v1/skills")
      .then((value) => { if (active()) setSkills(value); })
      .catch((e) => { if (active()) setError(String(e?.message || e)); });
  }, []);
  useEffect(() => {
    disposed.current = false;
    scans.current = new AbortController();
    load();
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

  function installed(fileId: string, title: string) {
    setTab("mine");
    settle(fileId, `安装 ${title}`);
  }

  function saved(fileId: string, label: string) {
    setEditing(null);
    setError("");
    settle(fileId, label);
  }

  async function replace(s: Skill, file: File) {
    setEditing(null);
    setError("");
    const form = new FormData();
    form.append("upload", file);
    const head = s.file_id!;
    setScanning((ids) => new Set(ids).add(head)); // no second replacement while this one uploads
    // a new version of the same file lineage: the package is replaced whole, scripts included
    const resp = await fetch(`${API_BASE}/v1/files?purpose=skill&parent_id=${encodeURIComponent(head)}`, { method: "POST", body: form }).finally(() =>
      setScanning((ids) => { const next = new Set(ids); next.delete(head); return next; })
    );
    redirectIfUnauthorized(resp);
    if (!resp.ok) {
      setError(`${file.name}：上传失败（${resp.status}）`);
      return;
    }
    const item: FileItem = await resp.json();
    settle(item.id, s.title);
  }

  async function toggle(s: Skill, enabled: boolean) {
    setBusy(s.name);
    setError("");
    setSkills((list) => list?.map((x) => (x.name === s.name && x.scope === s.scope ? { ...x, enabled } : x)) || list); // optimistic
    try {
      await api(`/v1/skills/${s.name}`, { method: "PUT", body: JSON.stringify({ enabled }) });
    } catch (e: any) {
      setError(`没能保存：${e?.message || e}`);
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

  const builtin = (skills || []).filter((s) => s.scope === "builtin");
  const personal = (skills || []).filter((s) => s.scope === "personal");

  return (
    <main className="page page-narrow">
      <div className="page-head">
        <h1>技能</h1>
        <p>技能是 AI 做特定工作时照着做的说明和工具。开启的技能会在需要时自动使用，也可以在输入框里直接点选；关闭后 AI 不会再用它。</p>
      </div>
      {error ? <div className="alert error" style={{ marginBottom: 12 }}>{error}</div> : null}

      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        <button className={tab === "mine" ? "primary sm" : "secondary sm"} onClick={() => setTab("mine")}>
          我的技能
        </button>
        <button className={tab === "market" ? "primary sm" : "secondary sm"} onClick={() => setTab("market")}>
          技能市场
        </button>
      </div>

      {tab === "market" ? (
        <MarketPanel onInstalled={installed} />
      ) : (
      <>
      <h2 className="section-title">通用技能</h2>
      <p className="hint section-hint">平台提供，所有人都可以用。</p>
      <section className="card skill-list">
        {skills === null ? <div className="empty">加载中…</div> : builtin.map((s) => <SkillRow key={s.name} s={s} busy={busy === s.name} onToggle={(on) => toggle(s, on)} onOpen={() => setEditing({ mode: "open", skill: s })} />)}
      </section>

      <div className="section-title skill-editor-head" style={{ marginTop: 32 }}>
        个人技能
        {storage ? <span className="hint">已用 {formatBytes(storage.used_bytes)} · 剩余 {formatBytes(storage.available_bytes)}</span> : null}
        <span className="spacer" />
        <button className="link" onClick={downloadTemplate}>
          <IconDownload size={15} /> 下载模板
        </button>
        <button className="secondary sm" onClick={() => setEditing({ mode: "create", content: TEMPLATE })}>
          <IconPlus size={15} /> 新建
        </button>
        <button className="secondary sm" onClick={() => picker.current?.click()}>
          <IconUpload size={15} /> 上传技能
        </button>
        <input ref={picker} type="file" accept=".md,.zip" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
      </div>
      <p className="hint section-hint">支持 SKILL.md 或包含脚本与素材的 ZIP。</p>
      <section className="card skill-list">
        {uploading.map((n) => (
          <div key={n} className="skill-row off">
            <span className="skill-icon">
              <IconSparkles size={20} />
            </span>
            <div className="grow hint">正在上传并识别 {n}…</div>
          </div>
        ))}
        {skills !== null && personal.length === 0 && uploading.length === 0 ? (
          <div className="empty">
            <strong>还没有个人技能</strong>
            把你常用的做法写成 SKILL.md 上传，AI 就会按你的方式做事。不知道怎么写可以先下载模板。
          </div>
        ) : null}
        {personal.map((s) => (
          <SkillRow
            key={s.file_id}
            s={s}
            busy={busy === s.name}
            locked={isLocked(s, scanning)}
            onToggle={(on) => toggle(s, on)}
            onOpen={() => setEditing({ mode: "open", skill: s })}
            onDelete={() => remove(s)}
          />
        ))}
      </section>
      </>
      )}
      {editing ? (
        <SkillEditor
          target={editing}
          locked={editing.mode === "open" && editing.skill.scope === "personal" && isLocked(editing.skill, scanning)}
          onClose={() => setEditing(null)}
          onSaved={saved}
          onReplace={replace}
        />
      ) : null}
      <p className="hint" style={{ marginTop: 14 }}>开关对你的账号生效，换设备也一样。</p>
    </main>
  );
}
