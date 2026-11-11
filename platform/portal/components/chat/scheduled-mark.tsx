"use client";

import { useState } from "react";
import { occurrenceText } from "../../lib/schedules";
import { IconChevronRight, IconClock } from "../icons";

/** Where a scheduled task spoke: a time mark in the thread instead of a message the user never typed. The task's
 * instruction is one click away. */
export default function ScheduledMark({ schedule, prompt }: { schedule: { title: string; at: string }; prompt: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`sched-mark ${open ? "open" : ""}`}>
      <button type="button" className="sched-mark-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <IconClock size={13} />
        <span className="truncate">{schedule.title}</span>
        <time>{occurrenceText(schedule.at)}</time>
        <IconChevronRight size={12} className="sched-mark-chev" />
      </button>
      {open ? <div className="sched-mark-prompt">{prompt}</div> : null}
    </div>
  );
}
