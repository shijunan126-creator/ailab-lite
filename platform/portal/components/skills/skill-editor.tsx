"use client";

import { useCallback, useState } from "react";
import { API_BASE, redirectIfUnauthorized } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import Modal from "../library/modal";
import { buildSkillMarkdown, readSkillMarkdown, saveError, skillSlug } from "./skill-save";

/** A new personal skill as three fields: what it is called, when to use it, what to do. The SKILL.md format stays
 * out of sight; pasting a whole SKILL.md into the instructions fills the fields from it. A skill with scripts or
 * other files is uploaded as a ZIP. */
export default function SkillEditor({ onClose, onSaved }: { onClose: () => void; onSaved: (fileId: string, label: string) => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [name, setName] = useState(""); // only when pasted from a SKILL.md: otherwise made from the title on save
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const dirty = !!(title.trim() || description.trim() || body.trim());

  const close = useCallback(async () => {
    if (saving) return; // the new file id is needed to wait for its scan
    if (dirty && !(await confirmDialog({ title: "放弃这个技能？", action: "放弃", danger: true }))) return;
    onClose();
  }, [saving, dirty, onClose]);

  function changeBody(next: string) {
    const pasted = readSkillMarkdown(next);
    if (!pasted) return setBody(next);
    // a whole SKILL.md: its header fills the fields that are still empty, the rest is the instructions
    if (pasted.title && !title.trim()) setTitle(pasted.title);
    if (pasted.description && !description.trim()) setDescription(pasted.description);
    if (pasted.name) setName(pasted.name);
    setBody(pasted.body);
  }

  async function save() {
    if (saving) return;
    const missing = !title.trim() ? "名称" : !description.trim() ? "什么时候用" : !body.trim() ? "怎么做" : "";
    if (missing) return setError(`请填写「${missing}」`);
    const id = name || skillSlug(title);
    setSaving(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/v1/skills/content`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: id, content: buildSkillMarkdown({ name: id, title, description, body }), expected_file_id: null }),
        cache: "no-store",
      });
      redirectIfUnauthorized(resp);
      if (!resp.ok) return setError(saveError(resp.status, await resp.text()));
      const saved: { file_id: string } = await resp.json();
      onSaved(saved.file_id, title.trim());
    } catch (e: any) {
      setError(`保存失败：${e?.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  const footer = (
    <>
      <span className="spacer" />
      <button className="secondary sm" onClick={close} disabled={saving}>取消</button>
      <button className="sm" onClick={save} disabled={saving}>{saving ? "保存中…" : "保存"}</button>
    </>
  );

  return (
    <Modal title="新建技能" onClose={close} className="skill-editor skill-create" footer={footer}>
      {error ? <div className="alert error skill-editor-alert">{error}</div> : null}
      <form
        className="skill-form"
        onSubmit={(e) => { e.preventDefault(); save(); }}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && (e.key === "Enter" || e.key.toLowerCase() === "s")) { e.preventDefault(); save(); }
        }}
      >
        <label className="field">
          <span>名称</span>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：周报助手" maxLength={60} autoFocus disabled={saving} />
        </label>
        <label className="field">
          <span>什么时候用</span>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="例如：用户要写周报、整理一周工作时" maxLength={500} disabled={saving} />
        </label>
        <label className="field grow">
          <span>怎么做</span>
          <textarea
            className="skill-editor-text"
            value={body}
            onChange={(e) => changeBody(e.target.value)}
            placeholder={"写给 AI 的做法：步骤、格式、注意事项。\n也可以直接粘贴一份完整的 SKILL.md。"}
            spellCheck={false}
            disabled={saving}
          />
        </label>
      </form>
    </Modal>
  );
}
