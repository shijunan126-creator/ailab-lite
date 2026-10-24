"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, errorMessage } from "../../lib/api";
import { projectsChanged } from "../../lib/projects";
import { IconAsk, IconFolderInput, IconMore, IconShare } from "../icons";
import ShareDialog from "../share-dialog";
import { MoveDialog } from "./projects";

const MENU_W = 168;
const MENU_H = 128;

export function askHref(fileId: string, projectId?: string | null): string {
  return projectId ? `/p/${projectId}?files=${fileId}` : `/?files=${fileId}`;
}

/** "⋯" on a file the user owns: ask about it in a new conversation, move it into a project, share it. */
export default function FileMenu({ fileId, filename, projectId, onMoved }: {
  fileId: string;
  filename: string;
  projectId?: string | null;
  onMoved?: (projectId: string | null) => void;
}) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const [dialog, setDialog] = useState<"move" | "share" | null>(null);
  const [error, setError] = useState("");
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!at) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && (menu.current?.contains(e.target as Node) || button.current?.contains(e.target as Node))) return;
      if (e.type === "keydown" && (e as KeyboardEvent).key !== "Escape") return;
      setAt(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);

  function open() {
    if (at) return setAt(null);
    setError("");
    const r = button.current!.getBoundingClientRect();
    const below = r.bottom + 4 + MENU_H < window.innerHeight;
    setAt({ top: below ? r.bottom + 4 : r.top - 4 - MENU_H, left: Math.max(8, Math.min(window.innerWidth - MENU_W - 8, r.right - MENU_W)) });
  }

  async function move(target: string | null) {
    setDialog(null);
    if (target === (projectId || null)) return;
    try {
      await api("/v1/files/batch", { method: "POST", body: JSON.stringify({ action: "move", ids: [fileId], project_id: target }) });
      onMoved?.(target);
      projectsChanged();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <>
      <button ref={button} className="icon-btn act" aria-label={`更多操作 ${filename}`} aria-expanded={!!at} onClick={open}>
        <IconMore size={16} />
      </button>
      {error ? <span role="alert" className="alert error">{error}</span> : null}
      {at
        ? createPortal(
            <div ref={menu} className="pop-menu fixed" role="menu" style={{ top: at.top, left: at.left, width: MENU_W }}>
              <Link className="menu-item" role="menuitem" href={askHref(fileId, projectId)} onClick={() => setAt(null)}>
                <IconAsk size={15} /> 提问
              </Link>
              <button className="menu-item" role="menuitem" onClick={() => (setAt(null), setDialog("move"))}>
                <IconFolderInput size={15} /> 移到项目
              </button>
              <button className="menu-item" role="menuitem" onClick={() => (setAt(null), setDialog("share"))}>
                <IconShare size={15} /> 分享
              </button>
            </div>,
            document.body
          )
        : null}
      {dialog === "move" ? <MoveDialog title={`移动「${filename}」`} current={projectId || null} onPick={move} onClose={() => setDialog(null)} /> : null}
      {dialog === "share" ? <ShareDialog target={{ kind: "file", fileId }} title={filename} onClose={() => setDialog(null)} /> : null}
    </>
  );
}
