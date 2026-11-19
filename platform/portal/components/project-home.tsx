"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Conversation, downloadUrl, errorMessage, FileItem, Project } from "../lib/api";
import { fileStatusText, formatBytes, timeAgo } from "../lib/format";
import { deleteProject, OPEN_PROJECT_PANEL, renameProject, takeProjectPanel } from "../lib/projects";
import { conversationsChanged, CONVERSATIONS_CHANGED } from "./app-shell";
import { IconDownload, IconFolder, IconHistory, IconMore, IconPencil, IconTrash } from "./icons";
import ConversationMenu, { RenameConversationInput } from "./conversation-menu";
import ProjectBrief from "./project-brief";
import ProjectMemoryDialog from "./memory/project-memory";
import FileTile from "./library/file-tile";
import FileViewer from "./preview/file-viewer";
import { useLibrary } from "./library/use-library";

const CHAT_PAGE_SIZE = 100;

/** A project's front page: start a chat in it, and see what is already in it. */
export default function ProjectHome({ project, composer, error }: { project: Project; composer: React.ReactNode; error: string }) {
  const router = useRouter();
  const [tab, setTab] = useState<"chats" | "files">("chats");
  const [chats, setChats] = useState<Conversation[] | null>(null);
  const [chatCursor, setChatCursor] = useState<string | null>(null);
  const [loadingChats, setLoadingChats] = useState(false);
  const [chatError, setChatError] = useState("");
  const chatRequest = useRef<AbortController | null>(null);
  const chatPages = useRef(1);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(project.name);
  const [menu, setMenu] = useState(false);
  const [memory, setMemory] = useState(false);
  const menuBox = useRef<HTMLDivElement | null>(null);
  const [viewing, setViewing] = useState<FileItem | null>(null);
  const [renamingChat, setRenamingChat] = useState<string | null>(null);
  const { files, loaded, more: moreFiles, loading: loadingFiles, error: filesError, loadMore, reload } = useLibrary({
    category: "all", projectId: project.id,
  }, tab === "files");

  useEffect(() => setName(project.name), [project.name]);

  useEffect(() => {
    const open = () => takeProjectPanel(project.id, "memory") && setMemory(true);
    open();
    window.addEventListener(OPEN_PROJECT_PANEL, open);
    return () => window.removeEventListener(OPEN_PROJECT_PANEL, open);
  }, [project.id]);

  const loadChats = useCallback(async (after: string | null = null) => {
    if (after && chatRequest.current) return;
    chatRequest.current?.abort();
    const controller = (chatRequest.current = new AbortController());
    const { signal } = controller;
    setLoadingChats(true);
    setChatError("");
    try {
      const rows = new Map<string, Conversation>();
      let cursor = after;
      let pages = 0;
      const depth = after ? 1 : chatPages.current;
      do {
        const params = new URLSearchParams({ project_id: project.id, limit: String(CHAT_PAGE_SIZE + 1) });
        if (cursor) params.set("cursor", cursor);
        const page = await api<Conversation[]>(`/v1/conversations?${params}`, { signal });
        if (signal.aborted) return;
        const visible = page.slice(0, CHAT_PAGE_SIZE);
        for (const row of visible) rows.set(row.id, row);
        cursor = page.length > CHAT_PAGE_SIZE ? visible[visible.length - 1].id : null;
        pages++;
      } while (cursor && pages < depth);
      // Replay the loaded depth after mutations because pinning or renaming can move a row across pages.
      setChats((prev) => {
        if (!after || !prev) return [...rows.values()];
        const seen = new Set(prev.map((row) => row.id));
        return [...prev, ...Array.from(rows.values()).filter((row) => !seen.has(row.id))];
      });
      setChatCursor(cursor);
      chatPages.current = after ? chatPages.current + pages : pages;
    } catch (err) {
      if (!signal.aborted) setChatError(errorMessage(err));
    } finally {
      if (chatRequest.current === controller) {
        chatRequest.current = null;
        if (!signal.aborted) setLoadingChats(false);
      }
    }
  }, [project.id]);

  useEffect(() => {
    const reloadChats = () => void loadChats();
    reloadChats();
    window.addEventListener(CONVERSATIONS_CHANGED, reloadChats);
    return () => {
      chatRequest.current?.abort();
      window.removeEventListener(CONVERSATIONS_CHANGED, reloadChats);
    };
  }, [loadChats]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => menuBox.current && !menuBox.current.contains(e.target as Node) && setMenu(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  async function rename() {
    setRenaming(false);
    const value = name.trim();
    if (!value || value === project.name) return setName(project.name);
    await renameProject(project.id, value).catch(() => setName(project.name));
  }

  async function remove() {
    setMenu(false);
    if (!(await deleteProject(project))) return;
    conversationsChanged();
    router.push("/");
  }

  const listError = tab === "chats" ? chatError : filesError;
  const loadingList = tab === "chats" ? loadingChats : loadingFiles;
  const more = tab === "chats" ? !!chatCursor : moreFiles;
  const loadNext = tab === "chats" ? () => loadChats(chatError ? null : chatCursor) : filesError ? reload : loadMore;

  return (
    <div className="project-home">
      <div className="project-head">
        <span className="project-mark">
          <IconFolder size={20} />
        </span>
        {renaming ? (
          <input
            type="text"
            className="project-name-input"
            autoFocus
            maxLength={120}
            aria-label="项目名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) e.currentTarget.blur();
              if (e.key === "Escape") {
                setName(project.name);
                setRenaming(false);
              }
            }}
          />
        ) : (
          <h1 onDoubleClick={() => setRenaming(true)}>{name}</h1>
        )}
        <div className="head-menu" ref={menuBox}>
          <button className="icon-btn" onClick={() => setMenu(!menu)} aria-label="项目操作" aria-expanded={menu}>
            <IconMore size={18} />
          </button>
          {menu ? (
            <div className="pop-menu right" role="menu">
              <button
                className="menu-item"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  setRenaming(true);
                }}
              >
                <IconPencil size={15} /> 重命名
              </button>
              <button
                className="menu-item"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  setMemory(true);
                }}
              >
                <IconHistory size={15} /> 记忆
              </button>
              <button className="menu-item danger" role="menuitem" onClick={remove}>
                <IconTrash size={15} /> 删除项目
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <ProjectBrief project={project} />
      {error ? <div className="alert error">{error}</div> : null}
      {composer}

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "chats"} className={tab === "chats" ? "on" : ""} onClick={() => setTab("chats")}>
          对话{project.conversations ? <span className="tab-count">{project.conversations}</span> : null}
        </button>
        <button role="tab" aria-selected={tab === "files"} className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
          文件{project.files ? <span className="tab-count">{project.files}</span> : null}
        </button>
        <span className="spacer" />
        {tab === "files" && project.files ? (
          <Link className="tab-link" href={`/files?project=${project.id}`}>
            在资料库中管理
          </Link>
        ) : null}
      </div>

      {tab === "chats" ? (
        chats === null ? null : chats.length === 0 ? (
          <div className="project-empty">在上面开始这个项目的第一个对话</div>
        ) : (
          <div className="project-list">
            {chats.map((c) => c.id === renamingChat ? (
              <RenameConversationInput key={c.id} conv={c} className="project-rename" onDone={() => setRenamingChat(null)} />
            ) : (
              <div key={c.id} className="project-row conv-row">
                <Link className="grow name" href={`/c/${c.id}`}>
                  {c.title || "新对话"}
                </Link>
                <span className="meta">{c.last_status === "running" || c.last_status === "queued" ? "回答中…" : timeAgo(c.updated_at)}</span>
                <ConversationMenu conv={c} onRename={() => setRenamingChat(c.id)} onPinned={conversationsChanged} />
              </div>
            ))}
          </div>
        )
      ) : !loaded || (filesError && !files.length) ? null : files.length === 0 ? (
        <div className="project-empty">在这个项目的对话里上传或生成的文件会放在这里</div>
      ) : (
        <div className="project-list">
          {files.map((f) => (
            <div key={f.id} className="project-row">
              <button className="project-file" onClick={() => setViewing(f)} aria-label={`预览 ${f.filename}`}>
                <FileTile file={f} size={30} />
                <span className="grow name">{f.filename}</span>
              </button>
              <span className="meta">
                {fileStatusText(f) ?? `${formatBytes(f.size)} · ${timeAgo(f.created_at)}`}
              </span>
              {f.status === "ready" ? (
                <a className="icon-btn" href={downloadUrl(f.download_url)} aria-label={`下载 ${f.filename}`} title="下载">
                  <IconDownload size={16} />
                </a>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {more || listError ? (
        <div className="load-more" aria-busy={loadingList}>
          {listError ? <div className="alert error" role="alert">{listError}</div> : null}
          <button className="link" disabled={loadingList} onClick={loadNext}>
            {listError ? "重试" : "加载更多"}
          </button>
        </div>
      ) : null}
      {memory ? <ProjectMemoryDialog project={project} onClose={() => setMemory(false)} /> : null}
      {viewing ? (
        <FileViewer files={files} file={viewing} onOpen={setViewing} onClose={() => setViewing(null)} chatHref={(f) => `/p/${project.id}?files=${f.id}`} />
      ) : null}
    </div>
  );
}
