"use client";

// Skill drafts are made in the background (contract §3.3). The sidebar shows that one is being worked on or waits
// for the user; whoever starts or changes a draft announces it, and only then does anything poll.

import { useEffect, useState } from "react";
import { api, SkillDraft } from "./api";

export const DRAFTS_CHANGED = "ailab:skill-drafts";
const POLL_MS = 3000;
const POLL_FOR_MS = 10 * 60_000; // extraction is one or two model calls; a draft still working after this is left to a reload

export const working = (d: SkillDraft) => d.status === "queued" || d.status === "running" || d.status === "preparing";

export function draftsChanged() {
  window.dispatchEvent(new Event(DRAFTS_CHANGED));
}

export function draftLabel(d: SkillDraft): string {
  if (d.status === "failed") return "失败";
  if (d.status === "ready") return "待确认";
  if (d.stage === "unpacking" || d.status === "preparing") return "正在准备文件…";
  if (d.stage === "drafting") return "正在起草…";
  if (d.status === "queued") return "排队中…";
  return "正在读取对话…";
}

export async function extractSkill(conversationId: string): Promise<SkillDraft> {
  const draft = await api<SkillDraft>(`/v1/conversations/${conversationId}/skill-drafts`, { method: "POST", body: "{}" });
  draftsChanged();
  return draft;
}

/** The user's open drafts, polled while any of them is still being made. */
export function useActiveDrafts(): SkillDraft[] | null {
  const [drafts, setDrafts] = useState<SkillDraft[] | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let until = 0;
    const load = async () => {
      clearTimeout(timer);
      controller?.abort();
      const c = (controller = new AbortController());
      try {
        const list = await api<SkillDraft[]>("/v1/skill-drafts?active=1", { signal: c.signal });
        if (stopped || c.signal.aborted) return;
        setDrafts(list);
        if (list.some(working) && Date.now() < until) timer = setTimeout(load, POLL_MS);
      } catch {
        if (!stopped && !c.signal.aborted && Date.now() < until) timer = setTimeout(load, POLL_MS * 2);
      }
    };
    const changed = () => {
      until = Date.now() + POLL_FOR_MS;
      load();
    };
    changed();
    window.addEventListener(DRAFTS_CHANGED, changed);
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      window.removeEventListener(DRAFTS_CHANGED, changed);
    };
  }, []);
  return drafts;
}
