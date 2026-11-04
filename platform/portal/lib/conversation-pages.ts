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

/** Older pages are reusable only within the same server-confirmed branch revision. */
export function refreshConversation(previous: ConversationDetail | null, latest: ConversationDetail): ConversationDetail {
  if (!previous || previous.id !== latest.id) return latest;
  if (previous.branch_revision !== undefined && latest.branch_revision !== undefined && latest.branch_revision < previous.branch_revision) return previous;
  if (latest.branch_revision === undefined || previous.branch_revision !== latest.branch_revision || !latest.has_more || !latest.turns.length) return latest;
  const older = previous.turns.filter((run) => order(run, latest.turns[0]) < 0);
  return { ...latest, turns: mergeTurns(older, latest.turns),
    has_more: older.length ? previous.has_more : latest.has_more };
}


/** Shared by chat history and PDF export: a previous page must belong to the same branch. */
export function prependConversationPage(current: ConversationDetail, older: ConversationDetail): ConversationDetail | null {
  if (current.id !== older.id || current.branch_revision === undefined || current.branch_revision !== older.branch_revision) return null;
  return { ...current, turns: mergeTurns(older.turns, current.turns),
    previews: mergePreviews(older.previews, current.previews), has_more: older.has_more };
}
