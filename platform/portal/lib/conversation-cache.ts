import type { ConversationDetail } from "./api";

// The conversations opened in this tab, so going back to one shows it at once while the fresh copy loads.
// A few recent ones only: each holds up to a page of turns.
const LIMIT = 12;
const MAX_CHARS = 1_000_000;
const cache = new Map<string, { detail: ConversationDetail; size: number }>();
let size = 0;

export function cachedConversation(id: string): ConversationDetail | null {
  return cache.get(id)?.detail || null;
}

export function cacheConversation(detail: ConversationDetail): void {
  size -= cache.get(detail.id)?.size || 0;
  cache.delete(detail.id);
  const page = { ...detail, turns: detail.turns.slice(-20), has_more: detail.has_more || detail.turns.length > 20 };
  const chars = JSON.stringify(page).length;
  // Large answers remain fetchable without filling the cache with their duplicate copies.
  if (chars > MAX_CHARS) return;
  cache.set(detail.id, { detail: page, size: chars });
  size += chars;
  while (cache.size > LIMIT || size > MAX_CHARS) {
    const first = cache.keys().next().value!;
    size -= cache.get(first)!.size;
    cache.delete(first);
  }
}
