"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api, errorMessage, Page, PlatformSkill, PlatformSkillVersion, SkillDraft } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { draftsChanged } from "../../lib/skill-drafts";
import { skillIcon } from "../chat/composer";
import { IconHistory, IconPencil, IconPlus, IconTrash, IconUpload } from "../icons";
import Modal from "../library/modal";
import RowMenu from "../row-menu";
import DraftEditor from "../skills/draft-editor";

type Meta = Pick<PlatformSkill, "name" | "title" | "description" | "category" | "source_url" | "license">;
const EMPTY: Meta = { name: "", title: "", description: "", category: "", source_url: "", license: "" };

/** 技能广场 management: the platform skills every user can add. A new version is scanned, then published to all who follow it. */
export default function PlatformSkillsSection() {
  const [items, setItems] = useState<PlatformSkill[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<PlatformSkill | "new" | null>(null);
  const [versions, setVersions] = useState<PlatformSkill | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const uploadFor = useRef<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const page = await api<Page<PlatformSkill>>("/v1/admin/platform-skills", { signal });
      setItems(page.items);
      setCursor(page.next_cursor);
    } catch (e) {
      if (!signal?.aborted) setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function more() {
    if (!cursor) return;
    try {
      const page = await api<Page<PlatformSkill>>(`/v1/admin/platform-skills?after=${encodeURIComponent(cursor)}`);
      setItems((list) => [...(list || []), ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function upload(skillId: string, file: File) {
    setBusy(skillId);
    setError("");
    try {
      const form = new FormData();
      form.append("upload", file);
      const pending = await api<{ file_id: string }>(`/v1/admin/platform-skills/${encodeURIComponent(skillId)}/versions`, { method: "POST", body: form });
      // the Worker scans the version, then it is published: refresh until it is (or the scan fails), at most a minute
      for (let i = 0; i < 30; i++) {
        const page = await api<Page<PlatformSkill>>("/v1/admin/platform-skills");
        setItems(page.items);
        setCursor(page.next_cursor);
        if (page.items.some((x) => x.id === skillId && x.current_file_id === pending.file_id)) return;
        const v = await api<Page<PlatformSkillVersion>>(`/v1/admin/platform-skills/${encodeURIComponent(skillId)}/versions?limit=5`);
        const mine = v.items.find((x) => x.file_id === pending.file_id);
        if (mine?.error || (mine && mine.status !== "pending")) {
          if (mine.error) setError(`${file.name}：${mine.error}`);
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function editOnline(s: PlatformSkill) {
    setBusy(s.id);
    setError("");
    try {
      const created = await api<SkillDraft>(`/v1/admin/platform-skills/${encodeURIComponent(s.id)}/drafts`, { method: "POST" });
      draftsChanged();
      setDraft(created.id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove(s: PlatformSkill) {
    const ok = await confirmDialog({
      title: `删除「${s.title}」？`,
      body: "所有用户的广场技能里将不再有它；用户修改过的副本会保留为个人技能。",
      action: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/v1/admin/platform-skills/${encodeURIComponent(s.id)}`, { method: "DELETE" });
      setItems((list) => list?.filter((x) => x.id !== s.id) || list);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <span className="spacer" />
        <button className="sm" onClick={() => setEditing("new")}>
          <IconPlus size={15} /> 新建
        </button>
      </div>
      <input
        ref={picker}
        type="file"
        accept=".md,.zip"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          const id = uploadFor.current;
          e.target.value = "";
          if (file && id) upload(id, file);
        }}
      />
      {error ? <div className="alert error">{error}</div> : null}
      <div className="mlist">
        {items === null ? <div className="mlist-empty">加载中…</div> : null}
        {items?.length === 0 ? <div className="mlist-empty">还没有平台技能</div> : null}
        {(items || []).map((s) => {
          const Icon = skillIcon(s.name);
          return (
            <div key={s.id} className={`mrow ${s.current_file_id ? "" : "off"}`}>
              <span className="mrow-icon">{busy === s.id ? <span className="draft-spin" /> : <Icon size={17} />}</span>
              <button className="mrow-main" onClick={() => setEditing(s)} aria-label={`编辑 ${s.title}`}>
                <span className="mrow-title">
                  <span>{s.title}</span>
                  {s.category ? <span className="state">{s.category}</span> : null}
                  {s.current_file_id ? null : <span className="state">未发布</span>}
                </span>
                <span className="mrow-desc">{s.description || s.name}</span>
              </button>
              <div className="mrow-trail">
                <RowMenu
                  label={s.title}
                  actions={[
                    { label: "上传新版本", icon: <IconUpload size={15} />, onSelect: () => { uploadFor.current = s.id; picker.current?.click(); } },
                    ...(s.current_file_id ? [{ label: "在线编辑", icon: <IconPencil size={15} />, onSelect: () => editOnline(s) }] : []),
                    { label: "版本", icon: <IconHistory size={15} />, onSelect: () => setVersions(s) },
                    "sep" as const,
                    { label: "删除", icon: <IconTrash size={15} />, danger: true, onSelect: () => remove(s) },
                  ]}
                />
              </div>
            </div>
          );
        })}
      </div>
      {cursor ? (
        <button className="secondary sm market-more" onClick={more}>
          更多
        </button>
      ) : null}
      {editing ? (
        <MetaDialog
          skill={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async (saved, file) => {
            setEditing(null);
            if (file) await upload(saved.id, file);
            else await load();
          }}
        />
      ) : null}
      {versions ? <VersionsDialog skill={versions} onClose={() => setVersions(null)} onChanged={() => load()} /> : null}
      {draft ? (
        <DraftEditor
          key={draft}
          draftId={draft}
          onClose={() => setDraft(null)}
          onConfirmed={() => {
            setDraft(null);
            load();
          }}
        />
      ) : null}
    </section>
  );
}

function MetaDialog({ skill, onClose, onSaved }: { skill: PlatformSkill | null; onClose: () => void; onSaved: (s: PlatformSkill, file: File | null) => void }) {
  const [meta, setMeta] = useState<Meta>(skill ? { ...EMPTY, ...skill } : EMPTY);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof Meta) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setMeta((m) => ({ ...m, [key]: e.target.value }));
  const valid = !!meta.title.trim() && (!!skill || /^[a-z0-9][a-z0-9-]{0,39}$/.test(meta.name));

  // a SKILL.md already says what it is: fill what is still empty from its frontmatter
  async function pick(next: File | null) {
    setFile(next);
    if (!next || !next.name.toLowerCase().endsWith(".md")) return;
    const head = (await next.slice(0, 8192).text()).match(/^---\r?\n([\s\S]*?)\r?\n---/);
    if (!head) return;
    const field = (key: string) => head[1].match(new RegExp(`^${key}:\\s*(.+)$`, "m"))?.[1].trim().replace(/^["']|["']$/g, "") || "";
    setMeta((m) => ({
      ...m,
      name: m.name || field("name"),
      title: m.title || field("title"),
      description: m.description || field("description"),
    }));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const { name, ...rest } = meta;
      const body = Object.fromEntries(Object.entries(rest).map(([k, v]) => [k, v.trim()]));
      const saved = skill
        ? await api<PlatformSkill>(`/v1/admin/platform-skills/${encodeURIComponent(skill.id)}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api<PlatformSkill>("/v1/admin/platform-skills", { method: "POST", body: JSON.stringify({ name, ...body }) });
      onSaved(saved, file);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={skill ? skill.title : "新建平台技能"}
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="platform-skill-form" disabled={busy || !valid}>
            {skill ? "保存" : "创建"}
          </button>
        </>
      }
    >
      <form id="platform-skill-form" className="admin-form" onSubmit={save}>
        {error ? <div className="alert error">{error}</div> : null}
        <label className="field">
          <span>名称</span>
          <input type="text" value={meta.title} onChange={set("title")} autoFocus />
        </label>
        <label className="field">
          <span>标识</span>
          <input type="text" value={meta.name} onChange={set("name")} disabled={!!skill} placeholder="weekly-report" spellCheck={false} />
        </label>
        <label className="field">
          <span>说明</span>
          <textarea rows={3} value={meta.description} onChange={set("description")} />
        </label>
        <div className="field-pair">
          <label className="field">
            <span>分类</span>
            <input type="text" value={meta.category} onChange={set("category")} />
          </label>
          <label className="field">
            <span>许可</span>
            <input type="text" value={meta.license} onChange={set("license")} placeholder="MIT" />
          </label>
        </div>
        <label className="field">
          <span>来源</span>
          <input type="url" value={meta.source_url} onChange={set("source_url")} placeholder="https://github.com/…" />
        </label>
        {skill ? null : (
          <label className="field">
            <span>技能文件</span>
            <input type="file" accept=".md,.zip" onChange={(e) => pick(e.target.files?.[0] || null)} />
          </label>
        )}
      </form>
    </Modal>
  );
}

function VersionsDialog({ skill, onClose, onChanged }: { skill: PlatformSkill; onClose: () => void; onChanged: () => void }) {
  const [items, setItems] = useState<PlatformSkillVersion[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const base = `/v1/admin/platform-skills/${encodeURIComponent(skill.id)}`;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setItems((await api<Page<PlatformSkillVersion>>(`${base}/versions`, { signal })).items);
      } catch (e) {
        if (!signal?.aborted) setError(errorMessage(e));
      }
    },
    [base]
  );
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);
  // a version being scanned settles on its own: follow it while the dialog is open
  const scanning = !!items?.some((v) => v.status === "pending");
  useEffect(() => {
    if (!scanning) return;
    const controller = new AbortController();
    const timer = setInterval(() => load(controller.signal), 2000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [scanning, load]);

  async function publish(v: PlatformSkillVersion) {
    setBusy(true);
    setError("");
    try {
      await api(`${base}/publish/${encodeURIComponent(v.file_id)}`, { method: "POST" });
      await load();
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`${skill.title} · 版本`} onClose={onClose}>
      {error ? <div className="alert error">{error}</div> : null}
      <div className="mlist">
        {items === null ? <div className="mlist-empty">加载中…</div> : null}
        {items?.length === 0 ? <div className="mlist-empty">还没有版本</div> : null}
        {(items || []).map((v) => (
          <div key={v.file_id} className="mrow">
            <div className="mrow-main">
              <span className="mrow-title">
                <span>v{v.version} · {v.filename}</span>
                {v.current ? <span className="state ready">当前</span> : null}
              </span>
              <span className={`mrow-desc ${v.error ? "error" : ""}`}>
                {v.error || (v.status === "ready" ? new Date(v.created_at).toLocaleString("zh-CN") : "扫描中…")}
              </span>
            </div>
            <div className="mrow-trail">
              {v.status === "ready" && !v.error ? (
                <a className="link sm" href={`${API_BASE}${base}/versions/${encodeURIComponent(v.file_id)}/content`}>
                  下载
                </a>
              ) : null}
              {v.status === "ready" && !v.error && !v.current ? (
                <button className="secondary sm" disabled={busy} onClick={() => publish(v)}>
                  发布
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
