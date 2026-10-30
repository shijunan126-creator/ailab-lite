// Generated-UI control state, saved per run (`PUT /v1/runs/{id}/ui-state`): what a reader set on an answer's
// controls, tabs and table sort survives a reload, and print shows the same. Bounded the way the API checks it;
// whatever does not fit is left out rather than failing the save.
import { API_BASE } from "./api";

export type BlockState = Record<string, unknown>;
export type UIState = { v: 1; blocks: Record<string, BlockState> };

const MAX_ENTRIES = 64;
const MAX_KEY = 120;
const MAX_TEXT = 1000;
const MAX_LIST = 50;
const MAX_BYTES = 16384;
const DELAY = 600;

const scalar = (v: unknown): boolean =>
  v === null || typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v)) || (typeof v === "string" && v.length <= MAX_TEXT);

/** One block's snapshot reduced to the stored shape: scalars, and OpenUI's `{value, componentType}` field wrappers. */
export function boundBlock(snapshot: Record<string, unknown>): BlockState {
  const out: BlockState = {};
  let n = 0;
  for (const [key, value] of Object.entries(snapshot)) {
    if (n >= MAX_ENTRIES) break;
    if (!key || key.length > MAX_KEY) continue;
    if (scalar(value)) out[key] = value;
    else if (value && typeof value === "object" && !Array.isArray(value) && "value" in value) {
      const { value: inner, componentType } = value as { value: unknown; componentType?: unknown };
      const ok = scalar(inner) || (Array.isArray(inner) && inner.length <= MAX_LIST && inner.every(scalar));
      const type = typeof componentType === "string" && componentType.length <= 40 ? componentType : null;
      if (ok) out[key] = { value: inner, componentType: type };
      else continue;
    } else continue;
    n++;
  }
  return out;
}

// The latest state of each run seen on this page. It outlives an answer that scrolls out of view and comes back,
// and a conversation detail fetched before the last save landed.
const latest = new Map<string, UIState>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function put(runId: string, keepalive = false) {
  timers.delete(runId);
  const state = latest.get(runId);
  if (!state) return;
  const body = JSON.stringify(state);
  if (new Blob([body]).size > MAX_BYTES) return; // over the stored bound: keep it for this page only
  void fetch(`${API_BASE}/v1/runs/${encodeURIComponent(runId)}/ui-state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive,
  }).catch(() => undefined); // a lost save costs only the restore after a reload
}

if (typeof window !== "undefined") {
  // leaving the page sends what is still waiting for its debounce
  window.addEventListener("pagehide", () => {
    for (const runId of [...timers.keys()]) {
      clearTimeout(timers.get(runId));
      put(runId, true);
    }
  });
}

/** The state to start a run's blocks from: this page's newer copy if it has one, else what the server returned. */
export function startingState(runId: string, saved: UIState | null | undefined): UIState | null {
  return latest.get(runId) ?? (saved && saved.v === 1 && saved.blocks ? saved : null);
}

/** Records one block's new state and saves the run's state shortly after the last change. */
export function saveBlock(runId: string, index: number, snapshot: Record<string, unknown>, base: UIState | null) {
  const current = latest.get(runId) ?? base ?? { v: 1 as const, blocks: {} };
  latest.set(runId, { v: 1, blocks: { ...current.blocks, [String(index)]: boundBlock(snapshot) } });
  clearTimeout(timers.get(runId));
  timers.set(runId, setTimeout(() => put(runId), DELAY));
}
