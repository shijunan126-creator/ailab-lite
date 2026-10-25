"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorMessage, Person, Share } from "../lib/api";
import { IconCheck, IconLink, IconPrinter, IconUsers, IconX } from "./icons";
import Modal from "./library/modal";
import { confirmDialog } from "../lib/dialog";

type Target = { kind: "conversation"; conversationId: string; activeRunId?: string | null } | { kind: "file"; fileId: string };

function query(target: Target): string {
  return target.kind === "conversation" ? `conversation_id=${encodeURIComponent(target.conversationId)}` : `file_id=${encodeURIComponent(target.fileId)}`;
}

/** Colleagues by name, as the user types. A later keystroke cancels the lookup still on its way. */
function PeoplePicker({ picked, onPick }: { picked: Person[]; onPick: (p: Person) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Person[]>([]);
  const [at, setAt] = useState(0);
  useEffect(() => {
    const term = q.trim();
    if (!term) return setFound([]);
    const c = new AbortController();
    const timer = setTimeout(() => {
      api<Person[]>(`/v1/users/lookup?q=${encodeURIComponent(term.slice(0, 60))}`, { signal: c.signal })
        .then((rows) => !c.signal.aborted && (setFound(rows), setAt(0)))
        .catch(() => undefined);
    }, 150);
    return () => {
      clearTimeout(timer);
      c.abort();
    };
  }, [q]);
  const options = found.filter((p) => !picked.some((x) => x.id === p.id));
  const take = (p: Person) => {
    onPick(p);
    setQ("");
    setFound([]);
  };
  return (
    <div className="people-picker">
      <input
        type="text"
        value={q}
        placeholder="输入同事姓名或账号"
        aria-label="添加同事"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (!options.length || e.nativeEvent.isComposing) return;
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setAt((i) => (i + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
          } else if (e.key === "Enter") {
            e.preventDefault();
            take(options[Math.min(at, options.length - 1)]);
          }
        }}
      />
      {options.length ? (
        <div className="people-options" role="listbox" aria-label="同事">
          {options.map((p, i) => (
            <button key={p.id} role="option" aria-selected={i === at} className={i === at ? "on" : ""} onMouseMove={() => setAt(i)} onClick={() => take(p)}>
              <span className="avatar sm">{(p.display_name || p.username)[0]?.toUpperCase()}</span>
              <span className="grow">{p.display_name || p.username}</span>
              <span className="hint">{p.username}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Share a conversation (as it is now) or a file with colleagues, read-only. One share per thing; changing it changes who sees it. */
export default function ShareDialog({ target, title, onClose }: { target: Target; title: string; onClose: () => void }) {
  const [share, setShare] = useState<Share | null | undefined>(undefined);
  const [audience, setAudience] = useState<Share["audience"]>("people");
  const [people, setPeople] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(copiedTimer.current), []);

  useEffect(() => {
    const c = new AbortController();
    // the newest live share of this thing; the dialog keeps working on that one
    api<Share[]>(`/v1/shares/for?${query(target)}&limit=1`, { signal: c.signal })
      .then((rows) => {
        if (c.signal.aborted) return;
        const s = rows[0] || null;
        setShare(s || null);
        if (s) {
          setAudience(s.audience);
          setPeople(s.recipients);
        }
      })
      .catch((err) => !c.signal.aborted && (setShare(null), setError(errorMessage(err))));
    return () => c.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save(next: { audience?: Share["audience"]; people?: Person[]; refresh?: boolean }) {
    const a = next.audience ?? audience;
    const list = next.people ?? people;
    setAudience(a);
    setPeople(list);
    if (!share) return; // nothing shared yet: the choice waits for "创建链接"
    setBusy(true);
    setError("");
    try {
      setShare(
        await api<Share>(`/v1/shares/${share.id}`, {
          method: "PATCH",
          body: JSON.stringify({ audience: a, user_ids: list.map((p) => p.id), ...(next.refresh ? { refresh: true } : {}) }),
        })
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    setBusy(true);
    setError("");
    try {
      const body =
        target.kind === "conversation"
          ? { kind: "conversation", conversation_id: target.conversationId }
          : { kind: "file", file_id: target.fileId };
      setShare(await api<Share>("/v1/shares", { method: "POST", body: JSON.stringify({ ...body, audience, user_ids: people.map((p) => p.id) }) }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!share || !(await confirmDialog({ title: "停止分享？", body: "同事将无法再打开这个链接。", action: "停止分享", danger: true }))) return;
    setBusy(true);
    try {
      await api(`/v1/shares/${share.id}`, { method: "DELETE" });
      setShare(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const link = share ? new URL(share.url, window.location.origin).toString() : "";
  async function copy() {
    await navigator.clipboard.writeText(link).catch(() => undefined);
    setCopied(true);
    clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => setCopied(false), 1600);
  }

  const stale = share && target.kind === "conversation" && target.activeRunId && share.through_run_id !== target.activeRunId;
  const ready = audience === "everyone" || people.length > 0;
  // a conversation can also leave as paper: the print view's dialog saves it as PDF
  const pdf =
    target.kind === "conversation" ? (
      <button className="link" onClick={() => window.open(`/print/${target.conversationId}`, "_blank", "noopener")}>
        <IconPrinter size={15} /> 导出 PDF
      </button>
    ) : null;
  return (
    <Modal
      title={`分享「${title}」`}
      onClose={onClose}
      className="modal-sm share-dialog"
      footer={
        share ? (
          <>
            {pdf}
            <button className="link danger" onClick={revoke} disabled={busy}>
              停止分享
            </button>
            <span className="spacer" />
            {stale ? (
              <button className="secondary" onClick={() => save({ refresh: true })} disabled={busy}>
                更新到最新
              </button>
            ) : null}
            <button onClick={copy}>
              {copied ? <IconCheck size={15} /> : <IconLink size={15} />} {copied ? "已复制" : "复制链接"}
            </button>
          </>
        ) : (
          <>
            {pdf}
            {error ? <span className="alert error grow-alert">{error}</span> : <span className="spacer" />}
            <button className="secondary" onClick={onClose}>取消</button>
            <button onClick={create} disabled={busy || share === undefined || !ready}>
              {busy ? "创建中…" : "创建链接"}
            </button>
          </>
        )
      }
    >
      <div className="segmented share-audience" role="radiogroup" aria-label="谁能看">
        {(
          [
            ["people", "指定同事"],
            ["everyone", "所有同事"],
          ] as [Share["audience"], string][]
        ).map(([value, label]) => (
          <button key={value} type="button" role="radio" aria-checked={audience === value} className={audience === value ? "on" : ""} disabled={busy} onClick={() => save({ audience: value })}>
            {label}
          </button>
        ))}
      </div>
      <div className="collapse" data-open={audience === "people"} inert={audience !== "people"}>
        <div>
          <PeoplePicker picked={people} onPick={(p) => save({ people: [...people, p] })} />
          {people.length ? (
            <div className="people-chips">
              {people.map((p) => (
                <span key={p.id} className="attach-chip">
                  <IconUsers size={13} /> {p.display_name || p.username}
                  <button className="link" aria-label={`移除 ${p.display_name || p.username}`} onClick={() => save({ people: people.filter((x) => x.id !== p.id) })}>
                    <IconX size={12} />
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {share ? (
        <div className="share-link">
          <IconLink size={15} />
          <input type="text" readOnly value={link} aria-label="分享链接" onFocus={(e) => e.currentTarget.select()} />
        </div>
      ) : null}
      {share && error ? <div className="alert error">{error}</div> : null}
    </Modal>
  );
}
