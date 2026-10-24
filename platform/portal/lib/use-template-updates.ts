"use client";

import { useEffect, useRef } from "react";
import { api, Template } from "./api";

/** Readiness permits use immediately; the independent preview can replace its sketch later. */
export function useTemplateUpdates(templates: Template[], onUpdate: (fresh: Template[]) => void, selectedPendingId?: string | null) {
  const callback = useRef(onUpdate);
  callback.current = onUpdate;
  const ids = templates.filter((t) => t.status === "pending" || (t.status === "ready" && t.preview_status === "pending")).map((t) => t.id);
  if (selectedPendingId) ids.push(selectedPendingId);
  const key = [...new Set(ids)].sort().join(",");

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const deadline = Date.now() + 5 * 60_000;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function poll() {
      const fresh = await Promise.all(key.split(",").map((id) =>
        api<Template>(`/v1/templates/${id}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]) }).catch(() => null),
      ));
      if (controller.signal.aborted) return;
      callback.current(fresh.filter((t): t is Template => !!t));
      if (Date.now() < deadline) timer = setTimeout(poll, ++attempts < 5 ? 1500 : 5000);
    }
    timer = setTimeout(poll, 1500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [key]);
}
