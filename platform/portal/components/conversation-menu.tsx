"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, Conversation, errorMessage } from "../lib/api";
import { projectsChanged } from "../lib/projects";
import { conversationsChanged } from "./app-shell";
import { IconBook, IconCheck, IconFilePen, IconFolderInput, IconMore, IconPin, IconPinOff, IconShare, IconSparkles, IconTrash } from "./icons";
import ShareDialog from "./share-dialog";
import { MoveDialog } from "./library/projects";
import { confirmDialog } from "../lib/dialog";
import { placeMenu } from "../lib/menu-place";
import { extractSkill } from "../lib/skill-drafts";

/** The open conversation page follows a move made from the sidebar (its breadcrumb, where uploads go). */
export const CONVERSATION_MOVED = "ailab:conversation-moved";

const MENU_W = 200;
const MENU_H = 300;

/** "⋯" on a conversation: move it into / out of a project, or delete it. */
export default function ConversationMenu({ conv, onDeleted, onPinned, className = "" }: {
  conv: Conversation;
  onDeleted?: () => void;
  onPinned?: (pinnedAt: string | null) => void;
  className?: string;
}) {
  const [at, setAt] = useState<React.CSSProperties | null>(null);
  const [moving, setMoving] = useState(false);
  const [withFiles, setWithFiles] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [policy, setPolicy] = useState(conv.memory_policy || {});
  const [policyBusy, setPolicyBusy] = useState(false);
  useEffect(() => setPolicy(conv.memory_policy || {}), [conv.memory_policy]);
  const [error, setError] = useState("");
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const title = conv.title || "新对话";

  useEffect(() => {
    if (!at) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && (menu.current?.contains(e.target as Node) || button.current?.contains(e.target as Node))) return;
      setAt(null);
    };
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true); // a fixed menu would drift away from its row
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);

  // Fixed, not absolute: the sidebar list scrolls and would clip a menu that hangs out of it.
  function open() {
    if (at) return setAt(null);
    setError("");
    setAt(placeMenu(button.current!.getBoundingClientRect(), MENU_W, MENU_H));
  }

  async function move(projectId: string | null) {
    setMoving(false);
    if (projectId === (conv.project_id || null)) return;
    // the files it made go with it, so the project's library has what its conversations produced
    const ok = await api(`/v1/conversations/${conv.id}`, {
      method: "PATCH",
      body: JSON.stringify({ project_id: projectId, move_files: withFiles }),
    }).then(
      () => true,
      (err) => {
        setError(errorMessage(err));
        return false;
      }
    );
    if (ok) window.dispatchEvent(new CustomEvent(CONVERSATION_MOVED, { detail: { id: conv.id, project_id: projectId } }));
    conversationsChanged();
    projectsChanged();
  }

  async function pin() {
    setAt(null);
    const next = !conv.pinned_at;
    try {
      const saved = await api<Conversation>(`/v1/conversations/${conv.id}`, { method: "PATCH", body: JSON.stringify({ pinned: next }) });
      onPinned?.(saved?.pinned_at ?? (next ? new Date().toISOString() : null));
    } catch {
      setError(next ? "置顶失败，请重试" : "取消置顶失败，请重试");
    }
  }

  async function extract() {
    setAt(null);
    setError("");
    try {
      await extractSkill(conv.id); // made in the background; the sidebar's 技能 entry shows its progress
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function toggleMemory(key: "read" | "write") {
    if (policyBusy) return;
    setPolicyBusy(true);
    try {
      const next = { read: policy.read !== false, write: policy.write !== false, [key]: policy[key] === false };
      const saved = await api<Conversation>(`/v1/conversations/${conv.id}`, { method: "PATCH", body: JSON.stringify({ memory_policy: next }) });
      setPolicy(saved.memory_policy || {});
      conversationsChanged();
    } catch (e) { setError(errorMessage(e)); }
    finally { setPolicyBusy(false); }
  }

  async function remove() {
    setAt(null);
    if (!(await confirmDialog({ title: `删除对话「${title}」？`, body: "产出的文件仍保留在资料库。", action: "删除", danger: true }))) return;
    try {
      await api(`/v1/conversations/${conv.id}`, { method: "DELETE" });
    } catch {
      setError("删除失败，请重试");
      return;
    }
    onDeleted?.();
    conversationsChanged();
    if (conv.project_id) projectsChanged();
  }

  return (
    <>
      <button
        ref={button}
        className={`link conv-more ${at ? "open" : ""} ${className}`}
        aria-label={`更多操作 ${title}`}
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
          {onPinned ? (
            <button className="menu-item" role="menuitem" onClick={pin}>
              {conv.pinned_at ? <IconPinOff size={15} /> : <IconPin size={15} />} {conv.pinned_at ? "取消置顶" : "置顶"}
            </button>
          ) : null}
          <button
            className="menu-item"
            role="menuitem"
            onClick={() => {
              setAt(null);
              setMoving(true);
            }}
          >
            <IconFolderInput size={15} /> {conv.project_id ? "移动到…" : "移到项目"}
          </button>
          <button
            className="menu-item"
            role="menuitem"
            onClick={() => {
              setAt(null);
              setSharing(true);
            }}
          >
            <IconShare size={15} /> 分享
          </button>
          <button className="menu-item" role="menuitem" onClick={extract}>
            <IconSparkles size={15} /> 提取技能
          </button>
          <div className="menu-sep" role="separator" />
          <button className="menu-item" role="menuitemcheckbox" aria-checked={policy.read !== false} disabled={policyBusy} onClick={() => toggleMemory("read")}>
            <IconBook size={15} /> <span className="grow">使用已有记忆</span>
            <span className="menu-check">{policy.read !== false ? <IconCheck size={15} /> : null}</span>
          </button>
          <button className="menu-item" role="menuitemcheckbox" aria-checked={policy.write !== false} disabled={policyBusy} onClick={() => toggleMemory("write")}>
            <IconFilePen size={15} /> <span className="grow">允许记住本对话</span>
            <span className="menu-check">{policy.write !== false ? <IconCheck size={15} /> : null}</span>
          </button>
          <div className="menu-sep" role="separator" />
          <button className="menu-item danger" role="menuitem" onClick={remove}>
            <IconTrash size={15} /> 删除
          </button>
        </div>,
            document.body
          )
        : null}
      {sharing ? (
        <ShareDialog target={{ kind: "conversation", conversationId: conv.id, activeRunId: conv.active_run_id }} title={title} onClose={() => setSharing(false)} />
      ) : null}
      {moving ? (
        <MoveDialog
          title={`移动「${title}」`}
          current={conv.project_id || null}
          onPick={move}
          onClose={() => setMoving(false)}
          footer={
            <label className="check-row">
              <input type="checkbox" checked={withFiles} onChange={(e) => setWithFiles(e.target.checked)} />
              连同产出的文件
            </label>
          }
        />
      ) : null}
    </>
  );
}
