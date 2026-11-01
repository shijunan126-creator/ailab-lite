"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE, redirectIfUnauthorized, Skill, skillKind } from "../../lib/api";
import Modal from "../library/modal";
import { frontmatterName, loadError, saveError, SkillContent } from "./skill-save";
import { confirmDialog } from "../../lib/dialog";

export type EditorTarget = { mode: "create"; content: string } | { mode: "view"; skill: Skill };

/** Write a new SKILL.md, or read a platform skill's full text (a personal copy is edited as a draft, see draft-editor). */
export default function SkillEditor({
  target,
  onClose,
  onSaved,
  onCustomize,
}: {
  target: EditorTarget;
  onClose: () => void;
  onSaved: (fileId: string, label: string) => void;
  onCustomize?: (skill: Skill) => void;
}) {
  const skill = target.mode === "view" ? target.skill : null;
  const [doc, setDoc] = useState<SkillContent | null>(target.mode === "create" ? newDoc(target.content) : null);
  const [text, setText] = useState(target.mode === "create" ? target.content : "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!skill) return;
    const ctl = new AbortController();
    fetch(`${API_BASE}/v1/skills/content/${encodeURIComponent(skill.name)}`, { signal: ctl.signal, cache: "no-store" })
      .then(async (resp) => {
        redirectIfUnauthorized(resp);
        if (!resp.ok) throw new Error(resp.status === 404 ? "找不到这个技能" : loadError(resp.status, await resp.text()));
        const value: SkillContent = await resp.json();
        setDoc({ ...value, editable: false });
        setText(value.content ?? "");
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setError(String(e?.message || e));
      });
    return () => ctl.abort();
  }, [skill]);

  const editable = target.mode === "create";
  const dirty = !!doc && editable && text !== (doc.content ?? "");

  const close = useCallback(async () => {
    if (saving) return; // the new file id is needed to wait for its scan
    if (dirty && !(await confirmDialog({ title: "放弃未保存的修改？", action: "放弃", danger: true }))) return;
    onClose();
  }, [saving, dirty, onClose]);

  async function save() {
    if (!doc || !editable || saving) return;
    const name = frontmatterName(text);
    if (!name) {
      setError("SKILL.md 开头需要 name");
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
  const footer = editable ? (
    <>
      <span className="spacer" />
      <button className="secondary sm" onClick={close} disabled={saving}>
        取消
      </button>
      <button className="sm" onClick={save} disabled={saving}>
        {saving ? "保存中…" : "保存"}
      </button>
    </>
  ) : skill && onCustomize && skillKind(skill) !== "personal" ? (
    <>
      <span className="spacer" />
      <button className="sm" onClick={() => onCustomize(skill)}>{skillKind(skill) === "platform" ? "修改" : "另存为个人技能"}</button>
    </>
  ) : undefined;

  return (
    <Modal title={title} onClose={close} className="skill-editor" footer={footer}>
      {error ? <div className="alert error skill-editor-alert">{error}</div> : null}
      {doc === null ? (
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
