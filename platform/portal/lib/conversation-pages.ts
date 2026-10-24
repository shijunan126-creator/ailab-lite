import type { ConversationDetail, Preview, Run } from "./api";

// UTCModel emits Z timestamps; normalize fractional precision before lexical ordering.
const stamp = (value: string) => value.replace(/(?:\.(\d+))?Z$/, (_, fraction = "") => `.${fraction.padEnd(6, "0")}Z`);
const order = (a: Run, b: Run) => stamp(a.created_at).localeCompare(stamp(b.created_at)) || a.id.localeCompare(b.id);

/** Chronological, deduplicated turns; later snapshots replace the same run. */
export function mergeTurns(older: Run[], newer: Run[]): Run[] {
  const byId = new Map(older.map((run) => [run.id, run]));
  for (const run of newer) byId.set(run.id, run);
  return [...byId.values()].sort(order);
}

export function mergePreviews(older: Preview[], newer: Preview[]): Preview[] {
  const files = new Map(older.map((p) => [p.filename, p]));
  for (const p of newer) files.set(p.filename, p);
  return [...files.values()];
}

/** A latest-page refresh must not discard already-loaded history or its cursor. Inside the page it is the truth:
 * a turn it no longer lists (replaced by a retry) is gone. */
export function refreshConversation(previous: ConversationDetail | null, latest: ConversationDetail): ConversationDetail {
  if (!previous || previous.id !== latest.id || !latest.turns.length) return latest;
  const older = previous.turns.filter((run) => order(run, latest.turns[0]) < 0);
  return { ...latest, turns: mergeTurns(older, latest.turns),
    has_more: older.length ? previous.has_more : latest.has_more };
}
