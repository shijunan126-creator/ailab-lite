"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, errorMessage, Schedule } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { stateText, when } from "../../lib/schedules";
import { IconClock, IconPause, IconPencil, IconPlay, IconSend, IconTrash } from "../icons";
import RowMenu from "../row-menu";

const SETTLE_MS = 4_000; // the dispatcher ticks every 30 s: look a little after the time, then again until it ran
const RETRY_MS = 10_000;
const MAX_WAIT_MS = 6 * 3_600_000;

/** The tasks that reply in this conversation, above the composer. At a task's time the conversation picks up the
 * turn it starts, so an open page shows the answer arrive. */
export default function ScheduleStrip({
  conversationId,
  refreshKey,
  running,
  onArrived,
}: {
  conversationId: string;
  refreshKey: string; // changes when a turn starts or ends here: a tool call may have changed the tasks
  running: boolean;
  onArrived: () => void;
}) {
  const router = useRouter();
  const [tasks, setTasks] = useState<Schedule[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const list = await api<Schedule[]>(`/v1/schedules?conversation_id=${encodeURIComponent(conversationId)}`, { signal: controller.signal });
      if (!controller.signal.aborted) setTasks(list);
      return controller.signal.aborted ? null : list;
    } catch {
      return null; // the strip is a convenience: a failed refresh keeps what it showed
    }
  }, [conversationId]);

  useEffect(() => {
    setTasks([]);
    setError("");
  }, [conversationId]);
  useEffect(() => {
    void load();
  }, [load, refreshKey]);
  useEffect(() => () => request.current?.abort(), []);

  // wake at the earliest time a task here is due; once it has run, show its turn
  const due = tasks.filter((t) => t.enabled && t.next_run_at).map((t) => Date.parse(t.next_run_at!)).sort((a, b) => a - b)[0];
  const lastRuns = tasks.map((t) => t.last_run_id).join();
  useEffect(() => {
    if (due === undefined || running) return;
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    const check = async () => {
      const list = await load();
      if (disposed || !list) return;
      if (list.map((t) => t.last_run_id).join() !== lastRuns) onArrived();
      else if (list.some((t) => t.enabled && t.next_run_at && Date.parse(t.next_run_at) <= Date.now())) timer = setTimeout(check, RETRY_MS);
    };
    timer = setTimeout(check, Math.min(Math.max(due - Date.now(), 0) + SETTLE_MS, MAX_WAIT_MS));
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [due, running, lastRuns, load, onArrived]);

  if (!tasks.length) return null;

  async function act(task: Schedule, fn: () => Promise<unknown>) {
    setBusy(task.id);
    setError("");
    try {
      await fn();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="sched-strip" role="list" aria-label="本会话的定时任务">
      {tasks.map((t) => (
        <div key={t.id} role="listitem" className="sched-item">
          <RowMenu
            label={t.title}
            disabled={busy === t.id}
            className={`sched-pill ${t.enabled ? "" : "off"} ${t.due_at ? "waiting" : ""}`}
            actions={[
              { heading: stateText(t) },
              {
                label: "立即运行",
                icon: <IconSend size={15} />,
                disabled: running,
                onSelect: () => act(t, async () => {
                  await api(`/v1/schedules/${t.id}/run`, { method: "POST" });
                  onArrived();
                }),
              },
              t.enabled
                ? { label: "暂停", icon: <IconPause size={15} />, onSelect: () => act(t, () => api(`/v1/schedules/${t.id}`, { method: "PATCH", body: JSON.stringify({ enabled: false }) })) }
                : { label: "恢复", icon: <IconPlay size={15} />, onSelect: () => act(t, () => api(`/v1/schedules/${t.id}`, { method: "PATCH", body: JSON.stringify({ enabled: true }) })) },
              { label: "编辑", icon: <IconPencil size={15} />, onSelect: () => router.push(`/schedules?edit=${t.id}`) },
              "sep",
              {
                label: "删除",
                icon: <IconTrash size={15} />,
                danger: true,
                onSelect: async () =>
                  (await confirmDialog({ title: `删除定时任务「${t.title}」？`, action: "删除", danger: true })) &&
                  act(t, () => api(`/v1/schedules/${t.id}`, { method: "DELETE" })),
              },
            ]}
          >
            {t.enabled ? <IconClock size={13} /> : <IconPause size={13} />}
            <span className="sched-pill-when">{when(t.schedule)}</span>
            <span className="truncate">{t.title}</span>
          </RowMenu>
        </div>
      ))}
      {error ? <span className="sched-error" role="alert">{error}</span> : null}
    </div>
  );
}
