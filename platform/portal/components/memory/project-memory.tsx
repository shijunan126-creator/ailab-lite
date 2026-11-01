"use client";

import { useState } from "react";
import { api, errorMessage, MemoryMode, Project } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { projectsChanged } from "../../lib/projects";
import Switch from "../switch";
import { MemoryDialog } from "./memory-summary";

/** A project's memory: whether it keeps to itself, and what it remembers. */
export default function ProjectMemoryDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const [mode, setMode] = useState<MemoryMode>(project.memory_mode || "shared");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function change(isolated: boolean, reload: () => void) {
    const ok = await confirmDialog(
      isolated
        ? {
            title: "改为仅使用本项目记忆？",
            body: "本项目不再读取你的其他记忆，只来自本项目对话的记忆会移入本项目。",
            action: "仅用本项目",
          }
        : {
            title: "与你的记忆互通？",
            body: "本项目会读取你的其他记忆；本项目的记忆会整理进你的记忆，只与本项目相关的仍留在这里。",
            action: "互通",
          }
    );
    if (!ok) return;
    const next: MemoryMode = isolated ? "isolated" : "shared";
    setBusy(true);
    setError("");
    try {
      const saved = await api<Project>(`/v1/projects/${project.id}`, { method: "PATCH", body: JSON.stringify({ memory_mode: next }) });
      setMode(saved.memory_mode || next);
      projectsChanged();
      reload(); // the summary moves, and a merge may have started
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <MemoryDialog
      projectId={project.id}
      title="项目记忆"
      onClose={onClose}
      top={(reload) => (
        <>
          <div className="mlist memory-mode">
            <div className="mrow">
              <div className="mrow-main"><span className="mrow-title"><span>仅使用本项目记忆</span></span></div>
              <div className="mrow-trail">
                <Switch on={mode === "isolated"} label="仅使用本项目记忆" disabled={busy} onChange={(on) => change(on, reload)} />
              </div>
            </div>
          </div>
          {error ? <div className="alert error">{error}</div> : null}
        </>
      )}
    />
  );
}
