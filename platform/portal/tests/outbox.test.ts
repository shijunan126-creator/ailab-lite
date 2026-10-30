import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { discard, enqueue, remove, retry, useOutbox } from "../lib/outbox";
import { existingUpload, useUploads } from "../lib/uploads";
import { api } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorCode: (e: any) => e.code, errorMessage: (e: Error) => e.message }));
const request = vi.mocked(api);
const message = (prompt: string, conversationId: string | null = "conv") => ({
  prompt, conversationId, projectId: null, attachments: [] as string[], options: { mode: "work" as const },
  model: "test", vision: false, steer: false, queued: false,
});
const stage = (id: string) => useOutbox.getState().pending[id]?.stage;
beforeEach(() => { useOutbox.setState({ pending: {} }); useUploads.setState({ uploads: {} }); request.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); });

test("same-millisecond sends stay ordered and both join the conversation created by the first", async () => {
  vi.spyOn(Date, "now").mockReturnValue(1);
  let finish!: (value: any) => void;
  request.mockImplementation(async (path) => {
    if (path === "/v1/conversations") return { id: "new" } as any;
    const body = JSON.parse(request.mock.calls.at(-1)![1]!.body as string);
    if (body.prompt === "first") return new Promise((resolve) => { finish = resolve; });
    return { id: "run-second" } as any;
  });
  const first = enqueue(message("first", null));
  const second = enqueue(message("second", null));
  await vi.waitFor(() => expect(stage(first)).toBe("sending"));
  expect(request.mock.calls.map(([p]) => p)).toEqual(["/v1/conversations", "/v1/conversations/new/messages"]);
  finish({ id: "run-first" });
  await vi.waitFor(() => expect(stage(second)).toBe("sent"));
  expect(request.mock.calls.filter(([p]) => p === "/v1/conversations")).toHaveLength(1);
  expect(request.mock.calls.at(-1)![0]).toBe("/v1/conversations/new/messages");
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
