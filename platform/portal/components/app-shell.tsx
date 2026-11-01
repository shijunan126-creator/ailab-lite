"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api, API_BASE, Conversation, Me, Usage, redirectIfUnauthorized } from "../lib/api";
import { formatTokens } from "../lib/format";
import { useProjects } from "../lib/projects";
import { IconClock, IconPin, IconSearch, IconFolder, IconHistory, IconLibrary, IconLogOut, IconNewChat, IconPanel, IconPlus, IconSettings, IconShield, IconSparkles } from "./icons";
import ConversationMenu from "./conversation-menu";
import SearchDialog, { OPEN_SEARCH } from "./search";
import Inbox, { InboxButton, useNotices } from "./inbox";
import { OPEN_FILE } from "./file-cite";
import { useFileViewer } from "./preview/file-viewer";
import { NewProjectInput, RenameProjectInput } from "./library/projects";
import ProjectMenu from "./project-menu";
import { useActiveDrafts, working } from "../lib/skill-drafts";

/** Pages tell the sidebar that the conversation list changed (a new one, a title, a finished answer). */
export const CONVERSATIONS_CHANGED = "ailab:conversations";
export function conversationsChanged(): void {
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED));
}

/** A conversation got its title while its first answer is still being written: the sidebar shows it without a reload. */
export const CONVERSATION_TITLED = "ailab:conversation-titled";
export function conversationTitled(id: string, title: string): void {
  window.dispatchEvent(new CustomEvent(CONVERSATION_TITLED, { detail: { id, title } }));
}

/** The visible part of the page, kept in --app-h / --app-top. On iOS a keyboard shrinks the visual viewport but not
 * 100vh / 100dvh and slides it down the page; on a phone the chat is pinned to it, so the composer sits right above the
 * keyboard and everything returns when the keyboard closes. */
function useVisibleViewport() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const root = document.documentElement.style;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        root.setProperty("--app-h", `${Math.round(vv.height)}px`);
        root.setProperty("--app-top", `${Math.max(0, Math.round(vv.offsetTop))}px`);
      });
    };
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(frame);
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      root.removeProperty("--app-h");
      root.removeProperty("--app-top");
    };
  }, []);
}

function UserMenu({ me }: { me: Me | null }) {
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState<Usage | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    api<Usage>("/v1/usage").then(setUsage).catch(() => setUsage(null));
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  const name = me?.display_name || me?.username || "";
  const pct = usage ? Math.min(100, Math.round(usage.daily_tokens.percent)) : 0; // counts answers still in progress
  return (
    <div className="user-menu" ref={box}>
      {open ? (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <div className="user-pop-name">{name}</div>
            <div className="hint">{me?.username}</div>
          </div>
          {usage ? (
            <div className="user-pop-usage" title="Token 按模型倍率折算">
              <div className="meter-head">
                <span>今日额度已用 {pct}%</span>
                <span>
                  {formatTokens(usage.daily_tokens.used)} / {formatTokens(usage.daily_tokens.limit)}
                </span>
              </div>
              <div className="meter-bar">
                <div className={`meter-fill ${pct >= 100 ? "full" : pct >= 80 ? "warn" : ""}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          ) : null}
          <Link className="menu-item" href="/account" role="menuitem" onClick={() => setOpen(false)}>
            <IconSettings size={16} /> 账号设置
          </Link>
          {me?.can_admin ? (
            <Link className="menu-item" href="/admin" role="menuitem" onClick={() => setOpen(false)}>
              <IconShield size={16} /> 管理
            </Link>
          ) : null}
          <button className="menu-item" onClick={logout} role="menuitem">
            <IconLogOut size={16} /> 退出登录
          </button>
        </div>
      ) : null}
      {me ? (
        <button className="user-chip" onClick={() => setOpen(!open)} aria-expanded={open} title="账号">
          <span className="avatar">{(name[0] || "?").toUpperCase()}</span>
          <span className="user-name">{name}</span>
        </button>
      ) : null}
    </div>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [settled, setSettled] = useState(false);
  const [searching, setSearching] = useState(false);
  const cited = useFileViewer();
  const openCited = cited.open;
  useEffect(() => {
    const open = (e: Event) => {
      const { id, page } = (e as CustomEvent).detail || {};
      if (id) openCited([id], id, page);
    };
    window.addEventListener(OPEN_FILE, open);
    return () => window.removeEventListener(OPEN_FILE, open);
  }, [openCited]);
  // ⌘K / Ctrl+K anywhere opens the search; other parts of the page open it with OPEN_SEARCH
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearching((open) => !open);
      }
    };
    const open = () => setSearching(true);
    window.addEventListener("keydown", key);
    window.addEventListener(OPEN_SEARCH, open);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener(OPEN_SEARCH, open);
    };
  }, []);
  // Before the first paint: remembered on wide screens; narrow screens start closed (the sidebar covers the page there).
  // Transitions start only after that, so a closed sidebar does not show and then slide away on load.
  useLayoutEffect(() => {
    setCollapsed(window.innerWidth < 760 || localStorage.getItem("ailab.sidebar") === "closed");
    const frame = requestAnimationFrame(() => setSettled(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const [addingProject, setAddingProject] = useState(false);
  const [renamingProject, setRenamingProject] = useState<string | null>(null);
  const projects = useProjects();
  const current = pathname.startsWith("/c/") ? pathname.slice(3) : null;
  const currentProject = pathname.startsWith("/p/") ? pathname.slice(3) : null;
  const listController = useRef<AbortController | null>(null);
  const notices = useNotices(current);
  const drafts = useActiveDrafts();
  const draftsWorking = !!drafts?.some(working);
  const draftsWaiting = drafts?.filter((d) => !working(d)).length || 0;
  const [inboxAt, setInboxAt] = useState<HTMLElement | null>(null);
  useVisibleViewport();

  const load = useCallback(() => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    return api<Conversation[]>("/v1/conversations", { signal: controller.signal })
      .then((rows) => { if (!controller.signal.aborted) setConversations(rows); })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    api<Me>("/v1/auth/me").then(setMe).catch(() => setMe(null));
    load();
    const titled = (e: Event) => {
      const { id, title } = (e as CustomEvent).detail || {};
      setConversations((rows) => rows?.map((row) => (row.id === id && !row.title ? { ...row, title } : row)) || rows);
    };
    window.addEventListener(CONVERSATIONS_CHANGED, load);
    window.addEventListener(CONVERSATION_TITLED, titled);
    return () => {
      listController.current?.abort();
      window.removeEventListener(CONVERSATIONS_CHANGED, load);
      window.removeEventListener(CONVERSATION_TITLED, titled);
    };
  }, [load]);

  // a conversation that is still answering: keep its "回答中…" fresh
  useEffect(() => {
    if (!conversations?.some((c) => c.last_status === "running" || c.last_status === "queued")) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [conversations, load]);

  useEffect(() => {
    if (window.innerWidth < 760) setCollapsed(true); // on a phone, navigating closes the drawer
  }, [pathname]);

  const pinned = (conversations || []).filter((c) => c.pinned_at);
  const recent = (conversations || []).filter((c) => !c.pinned_at);
  const row = (c: Conversation) => (
    <div key={c.id} className={`conv-item ${c.id === current ? "on" : ""}`}>
      <Link href={`/c/${c.id}`} title={c.title}>
        <span className="conv-title" key={c.title || ""}>
          {c.pinned_at ? <IconPin size={12} /> : null}
          {c.title || "新对话"}
        </span>
        {c.last_status === "running" || c.last_status === "queued" ? <span className="conv-live" role="status" aria-label="回答中" title="回答中" /> : null}
      </Link>
      <ConversationMenu conv={c} onPinned={(pinned_at) => {
        listController.current?.abort();
        setConversations((rows) => rows?.map((r) => (r.id === c.id ? { ...r, pinned_at } : r)) || null);
        load();
      }} onDeleted={() => {
        listController.current?.abort();
        setConversations((rows) => rows?.filter((r) => r.id !== c.id) || null);
        if (c.id === current) router.push("/");
      }} />
    </div>
  );

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    if (window.innerWidth >= 760) localStorage.setItem("ailab.sidebar", next ? "closed" : "open");
  }

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""} ${settled ? "" : "settling"}`}>
      <aside className="sidebar" aria-label="侧栏">
        <div className="sidebar-head">
          <Link className="brand" href="/">
            <span className="brand-mark">A</span>
            AILab
          </Link>
          <span className="spacer" />
          <InboxButton unread={notices.unread} onOpen={(el) => setInboxAt(inboxAt ? null : el)} />
          <button className="icon-btn" onClick={toggle} aria-label="收起侧栏" title="收起侧栏">
            <IconPanel size={18} />
          </button>
        </div>
        <nav className="side-nav" aria-label="主导航">
          <Link href="/" className={`side-link ${pathname === "/" ? "on" : ""}`}>
            <IconNewChat size={17} /> 新对话
          </Link>
          <button type="button" className="side-link" onClick={() => setSearching(true)} title="搜索（⌘K）">
            <IconSearch size={17} /> 搜索
          </button>
          <Link href="/files" className={`side-link ${pathname.startsWith("/files") ? "on" : ""}`}>
            <IconLibrary size={17} /> 资料库
          </Link>
          <Link href="/skills" className={`side-link ${pathname.startsWith("/skills") ? "on" : ""}`}>
            <IconSparkles size={17} /> <span className="grow">技能</span>
            {draftsWorking ? <span className="side-badge busy" aria-label="正在提取技能" /> : draftsWaiting ? <span className="side-badge" aria-label={`${draftsWaiting} 个技能草稿`}>{draftsWaiting}</span> : null}
          </Link>
          <Link href="/memory" className={`side-link ${pathname.startsWith("/memory") ? "on" : ""}`}>
            <IconHistory size={17} /> 记忆
          </Link>
          <Link href="/schedules" className={`side-link ${pathname.startsWith("/schedules") ? "on" : ""}`}>
            <IconClock size={17} /> 定时任务
          </Link>
        </nav>
        <div className="side-scroll">
        <div className="side-section">
          项目
          <button className="icon-btn side-add" onClick={() => setAddingProject(true)} aria-label="新建项目" title="新建项目">
            <IconPlus size={14} />
          </button>
        </div>
        <nav className="side-projects" aria-label="项目">
          {addingProject ? (
            <NewProjectInput
              onCreated={(p) => {
                setAddingProject(false);
                router.push(`/p/${p.id}`);
              }}
              onCancel={() => setAddingProject(false)}
            />
          ) : null}
          {(projects || []).map((p) =>
            p.id === renamingProject ? (
              <RenameProjectInput key={p.id} project={p} onDone={() => setRenamingProject(null)} />
            ) : (
              <div key={p.id} className="project-item">
                <Link href={`/p/${p.id}`} className={`side-link project ${p.id === currentProject ? "on" : ""}`} title={p.name}>
                  <IconFolder size={16} /> <span className="conv-title">{p.name}</span>
                </Link>
                <ProjectMenu
                  project={p}
                  onRename={() => setRenamingProject(p.id)}
                  onOpen={() => router.push(`/p/${p.id}`)}
                  onDeleted={async () => {
                    conversationsChanged();
                    if (p.id === currentProject) return router.push("/");
                    if (!current) return;
                    // The sidebar lists unassigned conversations only, so it cannot tell whether this one was deleted.
                    const response = await fetch(`${API_BASE}/v1/conversations/${current}?limit=1`, {
                      cache: "no-store", signal: AbortSignal.timeout(10000),
                    });
                    redirectIfUnauthorized(response);
                    await response.body?.cancel();
                    if (response.status === 404 && window.location.pathname === pathname) router.replace("/");
                  }}
                />
              </div>
            )
          )}
        </nav>
        {pinned.length ? (
          <>
            <div className="side-section">置顶</div>
            <nav className="conv-list" aria-label="置顶对话">
              {pinned.map(row)}
            </nav>
          </>
        ) : null}
        <div className="side-section">最近</div>
        <nav className="conv-list" aria-label="对话列表">
          {conversations && conversations.length === 0 ? <div className="hint conv-empty">还没有对话</div> : null}
          {recent.map(row)}
        </nav>
        </div>
        <UserMenu me={me} />
      </aside>
      <div className="sidebar-scrim" onClick={toggle} aria-hidden="true" />
      {searching ? <SearchDialog recent={conversations || []} onClose={() => setSearching(false)} /> : null}
      {cited.viewer}
      {inboxAt ? <Inbox anchor={inboxAt} onClose={() => setInboxAt(null)} onUnread={notices.setUnread} /> : null}

      <div className="main">
        {collapsed ? (
          <div className="main-top">
            <button className="icon-btn" onClick={toggle} aria-label="展开侧栏" title="展开侧栏">
              <IconPanel size={18} />
            </button>
            <Link className="icon-btn" href="/" aria-label="新对话" title="新对话">
              <IconNewChat size={18} />
            </Link>
            <InboxButton unread={notices.unread} onOpen={(el) => setInboxAt(inboxAt ? null : el)} />
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
}
