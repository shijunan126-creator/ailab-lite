"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorMessage, Project } from "../lib/api";
import { EDIT_BRIEF, projectsChanged, takeBriefEdit } from "../lib/projects";
import { IconFilePen } from "./icons";

const LIMIT = 4000; // the API enforces the same

/** The standing brief of a project: background, tone, audience. Every conversation in the project starts from it. */
export default function ProjectBrief({ project }: { project: Project }) {
  const saved = project.instructions || "";
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const field = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!editing) setText(saved);
  }, [saved, editing]);

  useEffect(() => {
    const open = () => takeBriefEdit(project.id) && setEditing(true);
    open();
    window.addEventListener(EDIT_BRIEF, open);
    return () => window.removeEventListener(EDIT_BRIEF, open);
  }, [project.id]);

  useEffect(() => {
    if (!editing || !field.current) return;
    const el = field.current;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, [editing]);

  async function save() {
    const value = text.trim();
    if (value === saved.trim()) return setEditing(false);
    setBusy(true);
    setError("");
    try {
      await api(`/v1/projects/${project.id}`, { method: "PATCH", body: JSON.stringify({ instructions: value }) });
      projectsChanged();
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setText(saved);
    setError("");
    setEditing(false);
  }

  if (!editing) {
    return (
      <button type="button" className={`project-brief ${saved ? "" : "empty"}`} onClick={() => setEditing(true)} aria-label="项目说明">
        <IconFilePen size={15} />
        <span className="project-brief-text">{saved || "添加项目说明"}</span>
      </button>
    );
  }

  return (
    <div className="project-brief editing">
      <textarea
        ref={field}
        rows={5}
        value={text}
        maxLength={LIMIT}
        aria-label="项目说明"
        placeholder="背景、语气、受众"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") cancel();
          else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
            e.preventDefault();
            save();
          }
        }}
      />
      <div className="project-brief-foot">
        {error ? <span className="alert error grow-alert">{error}</span> : <span className="hint">{text.length > LIMIT * 0.9 ? `${text.length}/${LIMIT}` : ""}</span>}
        <span className="spacer" />
        <button className="secondary" onClick={cancel} disabled={busy}>取消</button>
        <button onClick={save} disabled={busy}>{busy ? "保存中…" : "保存"}</button>
      </div>
    </div>
  );
}
