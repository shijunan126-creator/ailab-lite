// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { uploadFile } from "../lib/api";
import { getUpload, retryUpload, settle, startUpload, useUploads } from "../lib/uploads";

afterEach(() => { vi.unstubAllGlobals(); useUploads.setState({ uploads: {} }); });

test("library retries retain their operation; a new selection and explicit copy are independent", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "file", status: "ready" }) });
  vi.stubGlobal("fetch", fetch);
  const file = new File(["same"], "report.txt");
  await uploadFile(file, "project");
  await uploadFile(file, "project");
  await uploadFile(file, "project", "copy");
  await uploadFile(new File(["same"], "report.txt"), "project");
  const queries = fetch.mock.calls.map(([url]) => new URL(url, "http://test").searchParams);
  expect(queries[0].get("client_upload_id")).toBe(queries[1].get("client_upload_id"));
  expect(new Set([queries[0], queries[2], queries[3]].map(q => q.get("client_upload_id"))).size).toBe(3);
  expect(queries[2].get("disposition")).toBe("copy");
  await uploadFile(file, null, "copy", "skill");
  await uploadFile(file, null, "copy", "skill");
  const skillQueries = fetch.mock.calls.slice(-2).map(([url]) => new URL(url, "http://test").searchParams);
  expect(skillQueries[0].get("purpose")).toBe("skill");
  expect(skillQueries[0].get("client_upload_id")).toBe(skillQueries[1].get("client_upload_id"));
});

test("composer response loss retries the same operation in its original project", async () => {
  const requests: { url: string; xhr: FakeXHR }[] = [];
  class FakeXHR {
    status = 0; responseText = ""; upload = {}; timeout = 0;
    onload?: () => void; onerror?: () => void;
    open(_method: string, url: string) { requests.push({ url, xhr: this }); }
    send() {}
  }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  const key = startUpload(new File(["same"], "report.txt", { type: "text/plain" }), "original");
  requests[0].xhr.onerror!();
  await settle(key);
  expect(getUpload(key)?.stage).toBe("failed");
  expect(retryUpload(key, "different")).toBe(true);
  expect(requests[1].url).toBe(requests[0].url);
  expect(new URL(requests[0].url, "http://test").searchParams.get("client_upload_id")).toBe(key);
  requests[1].xhr.status = 201;
  requests[1].xhr.responseText = JSON.stringify({ id: "file", status: "ready" });
  requests[1].xhr.onload!();
  expect((await settle(key))?.fileId).toBe("file");
});
