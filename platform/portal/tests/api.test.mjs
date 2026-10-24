import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { getEventListeners } from "node:events";
import ts from "typescript";

// Run the dependency-free browser API module with Node's built-in test runner.
const source = await readFile(new URL("../lib/api.ts", import.meta.url), "utf8");
const compiled = ts.transpile(source, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 });
const { api, streamRun, filesByIds, archiveUrl } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("a successful 204 DELETE has no JSON body", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 204 }));
  assert.equal(await api("/v1/conversations/c", { method: "DELETE" }), undefined);
});

test("SSE reader releases its lock on stream.end", async (t) => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("event: stream.end\ndata: {}\n\n"));
      controller.close();
    },
  });
  t.mock.method(globalThis, "fetch", async () => new Response(body));
  let ended = 0;
  await streamRun("r", () => {}, new AbortController().signal, () => ended++);
  assert.equal(ended, 1);
  assert.equal(body.locked, false);
});

test("continuing a paused run starts after the events already displayed", async (t) => {
  const seen = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.match(String(url), /after=12$/);
    return sse("id: 12\nevent: a\ndata: {}\n\nid: 13\nevent: a\ndata: {}\n\nevent: stream.end\ndata: {}\n\n");
  });
  await streamRun("r", (e) => seen.push(e.seq), new AbortController().signal, undefined, 12);
  assert.deepEqual(seen, [13]);
});

test("filesByIds asks for all ids in one request, and none for an empty list", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(String(url));
    return new Response("[]", { status: 200 });
  });
  assert.deepEqual(await filesByIds([]), []);
  assert.equal(calls.length, 0);
  await filesByIds(["fil_a", "fil b"]);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /\/v1\/files\?limit=200&ids=fil_a&ids=fil%20b$/);
});

test("archiveUrl repeats and encodes ids", () => {
  assert.equal(archiveUrl(["a", "b&c"]), "/api/v1/files/archive?ids=a&ids=b%26c");
});

function sse(text) {
  return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(text)); c.close(); } }));
}

test("a dropped event stream resumes after the last event, without repeats", async (t) => {
  const urls = [];
  const replies = [
    () => sse("id: 1\nevent: a\ndata: {}\n\nid: 2\nevent: a\ndata: {}\n\n"), // connection drops, no stream.end
    () => sse("id: 2\nevent: a\ndata: {}\n\nid: 3\nevent: a\ndata: {}\n\nevent: stream.end\ndata: {}\n\n"),
  ];
  t.mock.method(globalThis, "fetch", async (url) => {
    urls.push(String(url));
    return replies.shift()();
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const seen = [];
  let ended = 0;
  const done = streamRun("r", (e) => seen.push(e.seq), new AbortController().signal, () => ended++);
  for (let i = 0; i < 20 && !ended; i++) {
    await new Promise((r) => setImmediate(r));
    t.mock.timers.tick(10_000);
  }
  await done;
  assert.deepEqual(seen, [1, 2, 3]);
  assert.equal(ended, 1);
  assert.match(urls[0], /after=0$/);
  assert.match(urls[1], /after=2$/);
});

test("the stream gives up after repeated failures, and stops at once when aborted", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    throw new TypeError("network down");
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const done = streamRun("r", () => {}, new AbortController().signal);
  const failed = done.then(() => false, () => true);
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setImmediate(r));
    t.mock.timers.tick(10_000);
  }
  assert.equal(await failed, true);
  assert.equal(calls, 8);

  const controller = new AbortController();
  const stopped = streamRun("r", () => {}, controller.signal);
  controller.abort();
  await stopped; // resolves quietly
});

test("empty successful SSE responses also exhaust the reconnect budget and release abort listeners", async (t) => {
  let calls = 0;
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return sse("");
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let result;
  const done = streamRun("r", () => {}, controller.signal).then(() => { result = "ended"; }, () => { result = "failed"; });
  try {
    for (let i = 0; i < 20 && !result; i++) {
      await new Promise((r) => setImmediate(r));
      t.mock.timers.tick(10_000);
      assert.ok(getEventListeners(controller.signal, "abort").length <= 1);
    }
  } finally {
    controller.abort(); // the regression must not leave a hung test on the old implementation
    await done;
  }
  assert.equal(result, "failed");
  assert.equal(calls, 8);
  assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});
