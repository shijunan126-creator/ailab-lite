// Client GenUI counters (HLD §6.14): batched, best effort, no ids. The API only increments Prometheus counters.
import { api } from "../../lib/api";
import { COMPONENT_NAMES } from "./catalog/limits";

type Outcome = "rendered" | "fallback" | "error" | "action";
type Item = { outcome: Outcome; component?: string; first_render_ms?: number };

let queue: Item[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function flush() {
  timer = null;
  const batch = queue.slice(0, 50);
  queue = queue.slice(50);
  if (batch.length) api("/v1/genui/telemetry", { method: "POST", body: JSON.stringify(batch), keepalive: true } as RequestInit).catch(() => undefined);
  if (queue.length) timer = setTimeout(flush, 6000);
}

export function report(outcome: Outcome, component?: string, firstRenderMs?: number) {
  if (typeof window === "undefined") return;
  queue.push({ outcome, ...(component && COMPONENT_NAMES.includes(component) ? { component } : {}), ...(firstRenderMs !== undefined ? { first_render_ms: Math.round(firstRenderMs) } : {}) });
  if (queue.length > 200) queue = queue.slice(-200);
  if (!timer) timer = setTimeout(flush, 6000);
}
