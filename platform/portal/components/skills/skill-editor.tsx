"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, downloadUrl, redirectIfUnauthorized, Skill } from "../../lib/api";
import Modal from "../library/modal";
import { IconDownload, IconUpload } from "../icons";
import { frontmatterName, loadError, saveError, SkillContent } from "./skill-save";
import { confirmDialog } from "../../lib/dialog";

export type EditorTarget = { mode: "create"; content: string } | { mode: "open"; skill: Skill };

/** View a skill's full text; edit and save a personal SKILL.md; download or replace a ZIP package as a whole. */
export default function SkillEditor({
  target,
  locked,
  onClose,
  onSaved,
  onReplace,
}: {
  target: EditorTarget;
  locked: boolean; // the head is still being scanned: no new version may branch from it
  onClose: () => void;
  onSaved: (fileId: string, label: string) => void;
  onReplace: (skill: Skill, file: File) => void;
}) {
  const skill = target.mode === "open" ? target.skill : null;
  const zip = skill?.scope === "personal" && skill.kind === "zip";
  const [doc, setDoc] = useState<SkillContent | null>(target.mode === "create" ? newDoc(target.content) : null);
  const [text, setText] = useState(target.mode === "create" ? target.content : "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const picker = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!skill || zip) return; // a ZIP has no text; the list already says which file it is
    const ctl = new AbortController();
    fetch(`${API_BASE}/v1/skills/content/${encodeURIComponent(skill.name)}`, { signal: ctl.signal, cache: "no-store" })
      .then(async (resp) => {
        redirectIfUnauthorized(resp);
        if (!resp.ok) throw new Error(resp.status === 404 ? "找不到这个技能" : loadError(resp.status, await resp.text()));
        const value: SkillContent = await resp.json();
        // the same name may also belong to an older, shadowed file: only the file that was opened may be edited
        const own = skill.scope === "builtin" || value.file_id === skill.file_id;
        setDoc({ ...value, editable: value.editable && own });
        setText(value.content ?? "");
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setError(String(e?.message || e));
      });
    return () => ctl.abort();
  }, [skill, zip]);

  const editable = !!doc?.editable && !locked;
  const dirty = !!doc && editable && text !== (doc.content ?? "");

  const close = useCallback(async () => {
    if (saving) return; // the new file id is needed to wait for its scan
    if (dirty && !(await confirmDialog({ title: "放弃未保存的修改？", action: "放弃", danger: true }))) return;
    onClose();
  }, [saving, dirty, onClose]);

  async function save() {
    if (!doc || !editable || saving) return;
    const name = frontmatterName(text);
    if (!name || (target.mode === "open" && name !== doc.name)) {
      setError(name ? `name 需保持为 ${doc.name}；改名请新建技能` : "SKILL.md 开头需要 name");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/v1/skills/content`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, content: text, expected_file_id: doc.file_id }),
        cache: "no-store",
      });
      redirectIfUnauthorized(resp);
      if (!resp.ok) {
        setError(saveError(resp.status, await resp.text()));
        return;
      }
      const saved: { file_id: string; name: string } = await resp.json();
      onSaved(saved.file_id, skill?.title || saved.name);
    } catch (e: any) {
      setError(`保存失败：${e?.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  const title = target.mode === "create" ? "新建技能" : skill!.title;
  const footer = zip ? (
    <>
      <span className="spacer" />
      <a className="btn secondary sm" href={downloadUrl(`/v1/files/${skill!.file_id}/content`)} download={skill!.filename}>
        <IconDownload size={15} /> 下载
      </a>
      <button className="sm" disabled={locked} onClick={() => picker.current?.click()}>
        <IconUpload size={15} /> 上传替换
      </button>
      <input
        ref={picker}
        type="file"
        accept=".zip"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file && skill) onReplace(skill, file);
        }}
      />
    </>
  ) : editable ? (
    <>
      <span className="spacer" />
      <button className="secondary sm" onClick={close} disabled={saving}>
        取消
      </button>
      <button className="sm" onClick={save} disabled={saving || (target.mode === "open" && !dirty)}>
        {saving ? "保存中…" : "保存"}
      </button>
    </>
  ) : undefined;

  return (
    <Modal title={title} onClose={close} className="skill-editor" footer={footer}>
      {error ? <div className="alert error skill-editor-alert">{error}</div> : null}
      {zip ? (
        <div className="skill-editor-zip">
          <div className="mono">{skill!.filename}</div>
          {skill!.files ? <div className="hint">{skill!.files} 个文件</div> : null}
          <ol className="skill-editor-steps">
            <li>下载并解压</li>
            <li>修改 SKILL.md、scripts/ 等文件</li>
            <li>重新打包为 .zip 上传替换</li>
          </ol>
        </div>
      ) : doc === null ? (
        error ? null : <div className="empty">加载中…</div>
      ) : (
        <textarea
          className="skill-editor-text mono"
          aria-label={`${title} SKILL.md`}
          value={text}
          readOnly={!editable || saving}
          autoFocus={editable}
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
              e.preventDefault();
              save();
            }
          }}
        />
      )}
    </Modal>
  );
}

function newDoc(content: string): SkillContent {
  return { name: "", content, file_id: null, editable: true, kind: "md", revision: null };
}
