// Quotes the user took from the side-pane viewer for the next message. One list per composer (conversation, or a
// new conversation of a project); they go out with that message as `doc_refs` and are cleared once it is handed over.
import { create } from "zustand";
import type { DocRef } from "./api";

export const MAX_DOC_REFS = 8;

export type Quote = { ref: DocRef; label: string };

type State = { quotes: Record<string, Quote[]> };
export const useQuotes = create<State>(() => ({ quotes: {} }));

const same = (a: DocRef, b: DocRef) => JSON.stringify(a) === JSON.stringify(b);

export function addQuote(key: string, quote: Quote) {
  useQuotes.setState((s) => {
    const list = (s.quotes[key] || []).filter((q) => !same(q.ref, quote.ref));
    return { quotes: { ...s.quotes, [key]: [...list, quote].slice(-MAX_DOC_REFS) } };
  });
}

export function removeQuote(key: string, index: number) {
  useQuotes.setState((s) => ({ quotes: { ...s.quotes, [key]: (s.quotes[key] || []).filter((_, i) => i !== index) } }));
}

export function clearQuotes(key: string) {
  useQuotes.setState((s) => {
    const { [key]: _, ...rest } = s.quotes;
    return { quotes: rest };
  });
}

export const quotesKey = (conversationId: string | null | undefined, projectId: string | null | undefined) => conversationId || `new:${projectId || ""}`;
