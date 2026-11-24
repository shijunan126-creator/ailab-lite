// The "@" file list behind the composer. Every keystroke asks the API, so: answers are cached for a short while per
// scope and query (typing back over a prefix is instant), and a request a newer keystroke has replaced is aborted.

import { api, type MentionItem } from "./api";

export type MentionScope = { conversationId?: string | null; projectId?: string | null };

const TTL = 15_000;
const LIMIT = 8;
const MAX_ENTRIES = 64;
const cache = new Map<string, { at: number; items: MentionItem[] }>();

export function mentionKey(scope: MentionScope, query: string): string {
  return `${scope.conversationId || ""}|${scope.projectId || ""}|${query.trim().normalize("NFKC").toLowerCase()}`;
}

export function cachedMentions(scope: MentionScope, query: string, now = Date.now()): MentionItem[] | null {
  const hit = cache.get(mentionKey(scope, query));
  return hit && now - hit.at < TTL ? hit.items : null;
}

/** Files a new upload or delivery may have changed: the next "@" asks again. */
export function forgetMentions() {
  cache.clear();
}

export async function fetchMentions(scope: MentionScope, query: string, signal?: AbortSignal): Promise<MentionItem[]> {
  const cached = cachedMentions(scope, query);
  if (cached) return cached;
  const params = new URLSearchParams({ q: query.trim(), limit: String(LIMIT) });
  if (scope.conversationId) params.set("conversation_id", scope.conversationId);
  else if (scope.projectId) params.set("project_id", scope.projectId);
  const { items } = await api<{ items: MentionItem[] }>(`/v1/files/mentions?${params}`, { signal });
  if (cache.size >= MAX_ENTRIES) cache.delete(cache.keys().next().value!);
  cache.set(mentionKey(scope, query), { at: Date.now(), items });
  return items;
}

/** The filename split into plain and matched pieces; offsets are code points, as the API counts them. */
export function highlightPieces(name: string, ranges: [number, number][]): { text: string; hit: boolean }[] {
  const chars = Array.from(name);
  const out: { text: string; hit: boolean }[] = [];
  let at = 0;
  for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
    const s = Math.max(at, Math.min(chars.length, start));
    const e = Math.max(s, Math.min(chars.length, end));
    if (s > at) out.push({ text: chars.slice(at, s).join(""), hit: false });
    if (e > s) out.push({ text: chars.slice(s, e).join(""), hit: true });
    at = e;
  }
  if (at < chars.length) out.push({ text: chars.slice(at).join(""), hit: false });
  return out;
}
