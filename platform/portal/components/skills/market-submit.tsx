"use client";

import { useState } from "react";
import { api, Skill } from "../../lib/api";

export default function MarketSubmit({
  skill,
  onClose,
  onSubmitted,
}: {
  skill: Skill;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const [title, setTitle] = useState(skill.title || skill.name);
  const [description, setDescription] = useState(skill.description || "");
  const [license, setLicense] = useState("NOASSERTION");
  const [version, setVersion] = useState("1.0.0");
  const [changelog, setChangelog] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!skill.file_id) return;
    setBusy(true);
    setError("");
    try {
      await api("/v1/market/submissions", {
        method: "POST",
        body: JSON.stringify({
          file_id: skill.file_id,
          title,
          description,
          category: "其他",
          license,
          version,
          changelog,
        }),
      });
      onSubmitted();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label="申请上架技能">
        <div className="modal-head">
          <h2>申请上架</h2>
          <button className="link" onClick={onClose} aria-label="关闭">
            关闭
          </button>
        </div>
        <div className="modal-body" style={{ gap: 12 }}>
          <p className="hint" style={{ margin: 0 }}>提交后进入管理员审核，通过后才会出现在技能市场。</p>
          {error ? <div className="alert error">{error}</div> : null}
          <label style={{ display: "grid", gap: 6 }}>
            <span>标题</span>
            <input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label style={{ display: "grid", gap: 6 }}>
            <span>说明</span>
            <textarea value={description} maxLength={1000} rows={4} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div style={{ display: "flex", gap: 12 }}>
            <label style={{ display: "grid", gap: 6, flex: 1 }}>
              <span>License</span>
              <input value={license} maxLength={80} onChange={(e) => setLicense(e.target.value)} />
            </label>
            <label style={{ display: "grid", gap: 6, width: 120 }}>
              <span>版本</span>
              <input value={version} maxLength={20} placeholder="1.0.0" onChange={(e) => setVersion(e.target.value)} />
            </label>
          </div>
          <label style={{ display: "grid", gap: 6 }}>
            <span>更新说明</span>
            <textarea value={changelog} maxLength={2000} rows={3} onChange={(e) => setChangelog(e.target.value)} />
          </label>
        </div>
        <div className="modal-foot">
          <span className="spacer" />
          <button className="secondary" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button onClick={submit} disabled={busy || !title.trim() || !description.trim() || !license.trim()}>
            {busy ? "提交中…" : "提交审核"}
          </button>
        </div>
      </div>
    </div>
  );
}
