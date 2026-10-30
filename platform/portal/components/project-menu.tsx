"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { errorMessage, Project } from "../lib/api";
import { deleteProject, requestBriefEdit } from "../lib/projects";
import { placeMenu } from "../lib/menu-place";
import { IconFilePen, IconMore, IconPencil, IconTrash } from "./icons";

const MENU_W = 176;
const MENU_H = 128;

/** "⋯" on a sidebar project: rename it in place, edit its brief, or delete it. */
export default function ProjectMenu({ project, onRename, onBrief, onDeleted }: {
  project: Project;
  onRename: () => void;
  onBrief: () => void;
  onDeleted: () => void | Promise<void>;
}) {
  const [at, setAt] = useState<React.CSSProperties | null>(null);
  const [error, setError] = useState("");
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!at) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && (menu.current?.contains(e.target as Node) || button.current?.contains(e.target as Node))) return;
      setAt(null);
    };
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);

  // Fixed and portalled like the conversation menu: the scrolling sidebar would clip it.
  function open() {
    if (at) return setAt(null);
    setError("");
    setAt(placeMenu(button.current!.getBoundingClientRect(), MENU_W, MENU_H));
  }

  async function remove() {
    setAt(null);
    try {
      if (await deleteProject(project)) await onDeleted();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <>
      <button
        ref={button}
        className={`link conv-more ${at ? "open" : ""}`}
        aria-label={`更多操作 ${project.name}`}
        aria-expanded={!!at}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          open();
        }}
      >
        <IconMore size={16} />
      </button>
      {error ? <span role="alert" className="alert error">{error}</span> : null}
      {at
        ? createPortal(
            <div ref={menu} className="pop-menu fixed" role="menu" style={at}>
              <button className="menu-item" role="menuitem" onClick={() => { setAt(null); onRename(); }}>
                <IconPencil size={15} /> 重命名
              </button>
              <button className="menu-item" role="menuitem" onClick={() => { setAt(null); requestBriefEdit(project.id); onBrief(); }}>
                <IconFilePen size={15} /> 项目说明
              </button>
              <button className="menu-item danger" role="menuitem" onClick={remove}>
                <IconTrash size={15} /> 删除项目
              </button>
            </div>,
            document.body
          )
        : null}
    </>
  );
}
