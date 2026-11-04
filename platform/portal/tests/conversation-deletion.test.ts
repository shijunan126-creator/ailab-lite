// @vitest-environment jsdom
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { test } from "vitest";
import ts from "typescript";
import { cacheConversation, cachedConversation, CONVERSATION_DELETED, forgetConversation, forgetProjectConversations } from "../lib/conversation-cache";

const source = await readFile(resolve("components/chat/chat.tsx"), "utf8");
const start = source.indexOf("  const loadDetail = useCallback(");
const load = source.slice(start, source.indexOf("  }, [id]);", start) + "  }, [id]);".length);
const removalStart = source.indexOf("    const removed = (event: Event)");
const removed = source.slice(removalStart, source.indexOf("    window.addEventListener(CONVERSATION_DELETED", removalStart));
const compiled = ts.transpile([load, removed].join("\n"), { target: ts.ScriptTarget.ES2022 });

function harness(id: string, api: () => Promise<unknown>) {
  let displayed: any = { id, project_id: "project", title: "cached", turns: [{ id: "run", summary: "deleted body" }] };
  cacheConversation(displayed);
  let error = "";
  let cleared = false;
  const controller = new AbortController();
  const noop = () => {};
  const vars = {
    id, detail: displayed, api, useCallback: (fn: unknown) => fn, unavailable: { current: false },
    branchMutation: { current: false }, loadSeq: { current: 0 }, detailWriteVersion: { current: 0 },
    detailController: { current: null }, olderController: { current: null },
    streamController: { current: new AbortController() }, actionController: { current: controller },
    runRefresh: { current: { controller: null, timer: null } }, visibleRevision: { current: 0 },
    setError: (next: string) => { error = next; }, errorMessage: (err: Error) => err.message,
    setDetail: (next: any) => { displayed = typeof next === "function" ? next(displayed) : next; },
    setStreaming: noop, setLoadingOlder: noop, setItems: noop, setReaders: noop, setReaderUrl: noop,
    setReaderOn: noop, setPaneOpen: noop, clearBranchPreviews: () => { cleared = true; },
    refreshConversation: (_previous: unknown, next: unknown) => next, forgetConversation,
  };
  const handlers = new Function(...Object.keys(vars), `${compiled}; return {loadDetail, removed};`)(...Object.values(vars));
  window.addEventListener(CONVERSATION_DELETED, handlers.removed);
  return { ...handlers, displayed: () => displayed, error: () => error, cleared: () => cleared, controller,
    close: () => window.removeEventListener(CONVERSATION_DELETED, handlers.removed) };
}

test("404 clears cached body and previews, aborts writers, and cannot be recached by a late response", async () => {
  const h = harness("gone-404", async () => { throw new Error("404 not found"); });
  try {
    await h.loadDetail();
    assert.equal(h.displayed(), null);
    assert.equal(cachedConversation("gone-404"), null);
    assert.equal(h.error(), "对话已删除或不存在");
    assert.ok(h.cleared() && h.controller.signal.aborted);
    cacheConversation({ id: "gone-404", turns: [] } as any);
    assert.equal(cachedConversation("gone-404"), null);
  } finally { h.close(); }
});

test("temporary network failures keep the usable conversation", async () => {
  const h = harness("network", async () => { throw new Error("network offline"); });
  try {
    await h.loadDetail();
    assert.equal(h.displayed().turns[0].summary, "deleted body");
    assert.ok(cachedConversation("network"));
    assert.equal(h.error(), "network offline");
    assert.equal(h.controller.signal.aborted, false);
  } finally { h.close(); }
});

test("project deletion clears open content even when a large conversation was not cached", async () => {
  let finish!: (value: unknown) => void;
  const h = harness("project-conversation", () => new Promise(resolve => { finish = resolve; }));
  try {
    cacheConversation({ id: "project-conversation", project_id: "project", turns: [{ summary: "x".repeat(1_000_001) }] } as any);
    assert.equal(cachedConversation("project-conversation"), null);
    const loading = h.loadDetail();
    forgetProjectConversations("project");
    finish({ id: "project-conversation", branch_revision: 10, turns: [{ summary: "late body" }] });
    await loading;
    assert.equal(h.displayed(), null);
    assert.equal(cachedConversation("project-conversation"), null);
    assert.ok(h.controller.signal.aborted);
  } finally { h.close(); }
});
