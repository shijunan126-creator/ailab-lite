import type { ConversationDetail } from "./api";

// The conversations opened in this tab, so going back to one shows it at once while the fresh copy loads.
// A few recent ones only: each holds up to a page of turns.
const LIMIT = 12;
const cache = new Map<string, ConversationDetail>();

export function cachedConversation(id: string): ConversationDetail | null {
  return cache.get(id) || null;
}

export function cacheConversation(detail: ConversationDetail): void {
  cache.delete(detail.id);
  cache.set(detail.id, detail);
  while (cache.size > LIMIT) cache.delete(cache.keys().next().value!);
}
