import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { discard, enqueue, pendingConversationId, remove, retry, useOutbox } from "../lib/outbox";
import { existingUpload, useUploads } from "../lib/uploads";
import { api } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorCode: (e: any) => e.code, errorMessage: (e: Error) => e.message }));
const request = vi.mocked(api);
const message = (prompt: string, conversationId: string | null = "conv") => ({
  prompt, conversationId, expectedBranchRevision: 0, projectId: null, attachments: [] as string[], options: { mode: "work" as const },
  model: "test", vision: false, steer: false, queued: false,
});
const stage = (id: string) => useOutbox.getState().pending[id]?.stage;
beforeEach(() => { useOutbox.setState({ pending: {} }); useUploads.setState({ uploads: {} }); request.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); });

test("same-millisecond sends stay ordered and both join the conversation created by the first", async () => {
  vi.spyOn(Date, "now").mockReturnValue(1);
  let finish!: () => void;
  request.mockImplementation(async (path) => {
    if (path === "/v1/conversations/start") return new Promise((resolve) => { finish = () => resolve({ conversation: { id: "new" }, run: { id: "run-first" } }); });
    return { id: "run-second" } as any;
  });
  const first = enqueue(message("first", null));
  const second = enqueue(message("second", null));
  await vi.waitFor(() => expect(stage(first)).toBe("sending"));
  expect(request.mock.calls.map(([p]) => p)).toEqual(["/v1/conversations/start"]);
  expect(JSON.parse(request.mock.calls[0][1]!.body as string)).toMatchObject({ prompt: "first", client_send_id: first });
  finish();
  await vi.waitFor(() => expect(stage(second)).toBe("sent"));
  expect(request.mock.calls.filter(([p]) => p === "/v1/conversations/start")).toHaveLength(1);
  expect(request.mock.calls.at(-1)![0]).toBe("/v1/conversations/new/messages");
});

test("a rejected first message stays on the new-chat page and a retry starts one conversation", async () => {
  request.mockRejectedValueOnce(Object.assign(new Error("too many active runs for this user"), { code: "active_runs" }));
  const id = enqueue(message("first", null));
  await vi.waitFor(() => expect(stage(id)).toBe("failed"));
  expect(useOutbox.getState().pending[id].sent?.conversation).toBeUndefined();
  expect(pendingConversationId(useOutbox.getState().pending[id])).toBeNull();

  request.mockResolvedValueOnce({ conversation: { id: "new" }, run: { id: "run" } });
  expect(retry(id)).toBe(true);
  await vi.waitFor(() => expect(stage(id)).toBe("sent"));
  expect(useOutbox.getState().pending[id].sent?.conversation?.id).toBe("new");
});

test("a failure that already created a conversation stays attached to that conversation", () => {
  const id = "legacy";
  useOutbox.setState({
    pending: {
      [id]: {
        ...message("legacy", null),
        id,
        stage: "failed",
        attempt: 1,
        created: 1,
        sent: { conversation: { id: "server-conversation" } as any },
      },
    },
  });
  expect(pendingConversationId(useOutbox.getState().pending[id])).toBe("server-conversation");
});

test("a lost send response retries with the same id and ready files, without another upload", async () => {
  const key = existingUpload({ id: "file", filename: "a.txt", content_type: "text/plain", status: "ready", size: 3 });
  request.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({ id: "run" });
  const id = enqueue({ ...message("files"), attachments: [key] });
  await vi.waitFor(() => expect(stage(id)).toBe("failed"));
  expect(retry(id)).toBe(true);
  await vi.waitFor(() => expect(stage(id)).toBe("sent"));
  const bodies = request.mock.calls.map(([, options]) => JSON.parse(options!.body as string));
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]).toMatchObject({ client_send_id: id, file_ids: ["file"] });
  remove(id);
  expect(useUploads.getState().uploads[key]).toBeUndefined();
});

test("a rejected attachment prevents dispatch; deleting its message releases its local state", async () => {
  const key = existingUpload({ id: "file", filename: "bad.txt", content_type: "text/plain", status: "rejected", size: 3 });
  const id = enqueue({ ...message("files"), attachments: [key] });
  await vi.waitFor(() => expect(stage(id)).toBe("failed"));
  expect(request).not.toHaveBeenCalled();
  discard(id);
  expect(useUploads.getState().uploads[key]).toBeUndefined();
});

 test.each([{ docRefs: undefined }, { docRefs: [] }, { docRefs: [{ file_id: "f", version: "v", at: "p:1", quote: "text" }] }])("doc_refs sent only when populated: %j", async ({ docRefs }) => {
  request.mockResolvedValue({ id: "run" });
  const id = enqueue({ ...message("quote"), docRefs });
  await vi.waitFor(() => expect(stage(id)).toBe("sent"));
  const body = JSON.parse(request.mock.calls.at(-1)![1]!.body as string);
  if (docRefs?.length) expect(body.doc_refs).toEqual(docRefs);
  else expect(body).not.toHaveProperty("doc_refs");
});


test("uploads and a lost-response retry keep the submitted branch fence", async () => {
  const key = existingUpload({ id: "file", filename: "a.txt", content_type: "text/plain", status: "ready", size: 3 });
  let prepared!: (value: any) => void;
  request.mockImplementationOnce(() => new Promise(resolve => { prepared = resolve; }));
  request.mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({}).mockResolvedValueOnce({ id: "run" });
  const outgoing = { ...message("frozen"), expectedBranchRevision: 4, attachments: [key], options: { mode: "chat" as const } };
  const id = enqueue(outgoing);
  await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
  outgoing.expectedBranchRevision = 5;
  prepared({});
  await vi.waitFor(() => expect(stage(id)).toBe("failed"));
  expect(retry(id)).toBe(true);
  // The attachment preparation runs again before the same dispatch is replayed.
  await vi.waitFor(() => expect(stage(id)).toBe("sent"));
  const sends = request.mock.calls.filter(([path]) => String(path).endsWith("/messages"));
  expect(sends).toHaveLength(2);
  for (const [, options] of sends) expect(JSON.parse(options!.body as string)).toMatchObject({ expected_branch_revision: 4, client_send_id: id });
});

test("a branch conflict stays failed instead of silently joining the other branch queue", async () => {
  request.mockRejectedValue(Object.assign(new Error("branch changed"), { code: "conversation_branch_changed" }));
  const id = enqueue({ ...message("old tab"), expectedBranchRevision: 2 });
  await vi.waitFor(() => expect(stage(id)).toBe("failed"));
  expect(request).toHaveBeenCalledTimes(1);
  expect(useOutbox.getState().pending[id].expectedBranchRevision).toBe(2);
});
