"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { api, Notice, NotificationKind } from "../lib/api";
import { timeAgo } from "../lib/format";
import { IconAsk, IconBell, IconCheck, IconClock, IconShare, IconSparkles, IconX } from "./icons";

const POLL_MS = 30_000;
const POP_W = 340;

const KIND: Record<NotificationKind, { label: string; icon: React.ReactNode; tone: string }> = {
  "run.succeeded": { label: "已完成", icon: <IconCheck size={15} />, tone: "ok" },
  "run.failed": { label: "未完成", icon: <IconX size={15} />, tone: "danger" },
  "run.waiting": { label: "等你回复", icon: <IconAsk size={15} />, tone: "warn" },
  "schedule.succeeded": { label: "定时任务已完成", icon: <IconClock size={15} />, tone: "ok" },
  "schedule.failed": { label: "定时任务未完成", icon: <IconClock size={15} />, tone: "danger" },
  "schedule.paused": { label: "定时任务已暂停", icon: <IconClock size={15} />, tone: "warn" },
  "share.received": { label: "分享给你", icon: <IconShare size={15} />, tone: "info" },
  "skill_draft.ready": { label: "技能草稿待确认", icon: <IconSparkles size={15} />, tone: "ok" },
  "skill_draft.failed": { label: "技能提取失败", icon: <IconSparkles size={15} />, tone: "danger" },
};

export function noticeHref(n: Notice): string {
  if (n.kind === "share.received" && n.share_id) return `/s/${n.share_id}`;
  if (n.kind.startsWith("skill_draft.")) return "/skills";
  if (n.kind === "schedule.paused") return "/schedules";
  if (!n.conversation_id) return "/";
  return `/c/${n.conversation_id}${n.run_id ? `?run=${n.run_id}` : ""}`;
}

/** The unread count, kept fresh while the page is visible. A cheap indexed count every 30 s; the list loads only when opened.
 * When something new arrives while the tab is in the background, the system shows it (if the user allowed that). */
export function useNotices(current: string | null) {
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const latest = useRef<string | null | undefined>(undefined); // undefined: not polled yet, so the first answer is not "new"
  const controller = useRef<AbortController | null>(null);
  const currentRef = useRef(current);
  currentRef.current = current;

  const announce = useCallback(
    async (since: string | null) => {
      if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
      const res = await api<{ items: Notice[] }>("/v1/notifications?limit=5").catch(() => null);
      for (const n of (res?.items || []).filter((n) => !n.read_at && (!since || n.created_at > since)).reverse()) {
        const shown = new Notification(n.title || "AILab", { body: KIND[n.kind]?.label || "", tag: n.id });
        shown.onclick = () => {
          window.focus();
          router.push(noticeHref(n));
          shown.close();
        };
      }
    },
    [router]
  );

  const poll = useCallback(async () => {
    controller.current?.abort();
    const c = new AbortController();
    controller.current = c;
    try {
      const res = await api<{ unread: number; latest_at: string | null }>("/v1/notifications/unread", { signal: c.signal });
      if (c.signal.aborted) return;
      const before = latest.current;
      latest.current = res.latest_at;
      // the conversation open in front of the user is being read right now
      if (res.unread && currentRef.current && !document.hidden && before !== undefined && res.latest_at !== before) {
        const after = await api<{ unread: number }>("/v1/notifications/read", {
          method: "POST",
          body: JSON.stringify({ conversation_id: currentRef.current }),
        }).catch(() => null);
        if (!c.signal.aborted) setUnread(after ? after.unread : res.unread);
      } else {
        setUnread(res.unread);
      }
      if (before !== undefined && res.latest_at && res.latest_at !== before && document.hidden) announce(before);
    } catch {
      /* the next tick tries again */
    }
  }, [announce]);

  useEffect(() => {
    poll();
    // hidden tabs still poll, more slowly: a background finish is what the system notification is for
    let timer = setInterval(poll, POLL_MS);
    const wake = () => {
      clearInterval(timer);
      timer = setInterval(poll, document.hidden ? POLL_MS * 2 : POLL_MS);
      if (!document.hidden) poll();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", poll);
    return () => {
      clearInterval(timer);
      controller.current?.abort();
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", poll);
    };
  }, [poll]);

  // opening a conversation reads what was waiting for it
  useEffect(() => {
    if (!current || !unread) return;
    api<{ unread: number }>("/v1/notifications/read", { method: "POST", body: JSON.stringify({ conversation_id: current }) })
      .then((r) => setUnread(r.unread))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  return { unread, setUnread, refresh: poll };
}

export function InboxButton({ unread, onOpen, className = "" }: { unread: number; onOpen: (anchor: HTMLElement) => void; className?: string }) {
  return (
    <button
      className={`icon-btn inbox-btn ${className}`}
      onClick={(e) => onOpen(e.currentTarget)}
      aria-label={unread ? `通知（${unread} 条未读）` : "通知"}
      title="通知"
    >
      <IconBell size={18} />
      {unread ? <span className="inbox-badge">{unread > 99 ? "99+" : unread}</span> : null}
    </button>
  );
}

export default function Inbox({ anchor, onClose, onUnread }: { anchor: HTMLElement; onClose: () => void; onUnread: (n: number) => void }) {
  const router = useRouter();
  const [items, setItems] = useState<Notice[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left), window.innerWidth - POP_W - 8);
    setAt({ top: r.bottom + 6, left: Math.max(8, left), maxHeight: Math.min(480, window.innerHeight - r.bottom - 18) });
  }, [anchor]);

  useEffect(() => {
    // the browser asks once, on the user's own click, never on page load
    if (typeof Notification !== "undefined" && Notification.permission === "default") Notification.requestPermission().catch(() => undefined);
    const c = new AbortController();
    api<{ items: Notice[]; next_cursor: string | null; unread: number }>("/v1/notifications?limit=20", { signal: c.signal })
      .then((res) => {
        setItems(res.items);
        setCursor(res.next_cursor);
        onUnread(res.unread);
      })
      .catch(() => !c.signal.aborted && setItems([]));
    const close = (e: Event) => {
      if (e.type === "keydown" && (e as KeyboardEvent).key !== "Escape") return;
      if (e.type === "mousedown" && (box.current?.contains(e.target as Node) || anchor.contains(e.target as Node))) return;
      onClose();
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    window.addEventListener("resize", close);
    return () => {
      c.abort();
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
      window.removeEventListener("resize", close);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor]);

  async function loadMore() {
    if (!cursor || more) return;
    setMore(true);
    try {
      const res = await api<{ items: Notice[]; next_cursor: string | null }>(`/v1/notifications?limit=20&cursor=${encodeURIComponent(cursor)}`);
      setItems((rows) => [...(rows || []), ...res.items.filter((n) => !rows?.some((r) => r.id === n.id))]);
      setCursor(res.next_cursor);
    } finally {
      setMore(false);
    }
  }

  async function read(body: { ids?: string[]; all?: boolean }) {
    const now = new Date().toISOString();
    setItems((rows) => rows?.map((n) => (body.all || body.ids?.includes(n.id) ? { ...n, read_at: n.read_at || now } : n)) || rows);
    const res = await api<{ unread: number }>("/v1/notifications/read", { method: "POST", body: JSON.stringify(body) }).catch(() => null);
    if (res) onUnread(res.unread);
  }

  function open(n: Notice) {
    if (!n.read_at) read({ ids: [n.id] });
    onClose();
    router.push(noticeHref(n));
  }

  if (!at) return null;
  const unread = items?.some((n) => !n.read_at);
  return createPortal(
    <div ref={box} className="pop-menu fixed inbox" role="dialog" aria-label="通知" style={{ top: at.top, left: at.left, width: POP_W, maxHeight: at.maxHeight }}>
      <div className="inbox-head">
        <strong>通知</strong>
        <span className="spacer" />
        {unread ? (
          <button className="link" onClick={() => read({ all: true })}>
            全部已读
          </button>
        ) : null}
      </div>
      <div
        className="inbox-list"
        onScroll={(e) => {
          const el = e.currentTarget;
          if (el.scrollTop + el.clientHeight > el.scrollHeight - 40) loadMore();
        }}
      >
        {items === null ? (
          <div className="pr-loading"><span /><span /><span /></div>
        ) : items.length === 0 ? (
          <div className="inbox-empty">没有通知</div>
        ) : (
          items.map((n) => {
            const kind = KIND[n.kind] || KIND["run.succeeded"];
            return (
              <button key={n.id} className={`inbox-row ${n.read_at ? "" : "unread"}`} onClick={() => open(n)}>
                <span className={`inbox-icon ${kind.tone}`}>{kind.icon}</span>
                <span className="inbox-text">
                  <span className="inbox-title">{n.title || "新对话"}</span>
                  <span className="inbox-kind">{kind.label}</span>
                </span>
                <span className="inbox-when">{timeAgo(n.created_at)}</span>
              </button>
            );
          })
        )}
        {more ? <div className="pr-loading"><span /><span /><span /></div> : null}
      </div>
    </div>,
    document.body
  );
}
