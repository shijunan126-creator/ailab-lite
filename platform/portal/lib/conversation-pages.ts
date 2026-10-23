import type { ConversationDetail, Preview, Run } from "./api";

/** Chronological, deduplicated turns; later snapshots replace the same run. */
export function mergeTurns(older: Run[], newer: Run[]): Run[] {
  const byId = new Map(older.map((run) => [run.id, run]));
  for (const run of newer) byId.set(run.id, run);
  // UTCModel emits Z timestamps; normalize fractional precision before lexical ordering.
  const stamp = (value: string) => value.replace(/(?:\.(\d+))?Z$/, (_, fraction = "") => `.${fraction.padEnd(6, "0")}Z`);
  return [...byId.values()].sort((a, b) => stamp(a.created_at).localeCompare(stamp(b.created_at)) || a.id.localeCompare(b.id));
}

export function mergePreviews(older: Preview[], newer: Preview[]): Preview[] {
  const files = new Map(older.map((p) => [p.filename, p]));
  for (const p of newer) files.set(p.filename, p);
  return [...files.values()];
}

/** A latest-page refresh must not discard already-loaded history or its cursor. */
export function refreshConversation(previous: ConversationDetail | null, latest: ConversationDetail): ConversationDetail {
  if (!previous || previous.id !== latest.id) return latest;
  const extended = previous.turns.length && latest.turns.length &&
    mergeTurns(previous.turns, latest.turns)[0].id !== latest.turns[0].id;
  return { ...latest, turns: mergeTurns(previous.turns, latest.turns),
    previews: mergePreviews(previous.previews, latest.previews), has_more: extended ? previous.has_more : latest.has_more };
}
