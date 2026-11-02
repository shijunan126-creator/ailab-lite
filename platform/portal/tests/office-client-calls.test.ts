import { beforeEach, expect, test, vi } from "vitest";
import { api } from "../lib/api";
import { captureView, hasView } from "../lib/office-views";
import { runJs } from "../lib/js/runner";
import { handleClientCall } from "../lib/js/client-calls";
vi.mock("../lib/api", () => ({ api: vi.fn() }));
vi.mock("../lib/office-views", () => ({ captureView: vi.fn(), hasView: vi.fn() }));
vi.mock("../lib/js/runner", () => ({ runJs: vi.fn() }));
let seq = 0;
const shot = { data: "png", width: 800, height: 600, at: "p:1", version: "hash" };
const call = () => ({ call_id: `c${++seq}`, kind: "capture_view", input: { view_id: "view", at: "p:1", max_width: 1600 }, timeout_ms: 1000 });
const result = () => JSON.parse(vi.mocked(api).mock.calls.at(-1)![1]!.body as string);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api).mockResolvedValue({ claim_token: "token" }); vi.mocked(hasView).mockReturnValue(true); vi.mocked(captureView).mockResolvedValue(shot); });
test("only the tab holding the view claims; reconnect replay is ignored", async () => {
  const c = call(); vi.mocked(hasView).mockReturnValue(false);
  await handleClientCall("run", c); expect(api).not.toHaveBeenCalled();
  vi.mocked(hasView).mockReturnValue(true); await handleClientCall("run", c); await handleClientCall("run", c);
  expect(api).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api).mock.calls[0][0]).toContain(`/${c.call_id}/claim`);
  expect(result()).toEqual({ claim_token: "token", ok: true, value: { image: "png", mime_type: "image/png", width: 800, height: 600, at: "p:1", version: "hash" } });
});
test("oversized base64 retakes narrower, at the exact cap it succeeds", async () => {
  const cap = Math.floor(2 * 1024 * 1024 * 4 / 3);
  vi.mocked(captureView).mockResolvedValueOnce({ ...shot, data: "x".repeat(cap + 1) }).mockResolvedValueOnce({ ...shot, data: "x".repeat(cap) });
  await handleClientCall("run", call());
  expect(vi.mocked(captureView).mock.calls.map(c => c[2])).toEqual([1600, 960]);
  expect(result().ok).toBe(true);
});
test("closed viewer after claim returns error", async () => {
  vi.mocked(captureView).mockReturnValue(null);
  await handleClientCall("run", call()); expect(result()).toMatchObject({ ok: false, error: "查看器已关闭" });
});
test("capture rejection and an image still oversized at minimum width fail explicitly", async () => {
  vi.mocked(captureView).mockRejectedValueOnce(new Error("closed"));
  await handleClientCall("run", call()); expect(result()).toMatchObject({ ok: false, error: "closed" });
  vi.mocked(captureView).mockResolvedValue({ ...shot, data: "x".repeat(2800000) });
  await handleClientCall("run", call()); expect(result()).toMatchObject({ ok: false, error: "截图过大" });
  expect(vi.mocked(captureView).mock.calls.at(-1)![2]).toBe(480);
});
test("js calls retain code, input, limits, logs and stats", async () => {
  const reply = { id: "job", ok: true as const, value: "3", logs: "", stats: { ms: 1 } };
  vi.mocked(runJs).mockResolvedValue(reply);
  await handleClientCall("run", { call_id: `js${++seq}`, kind: "js", code: "return 3", input: { x: 1 }, timeout_ms: 100, limits: { output_kb: 100 } });
  expect(runJs).toHaveBeenCalledWith({ code: "return 3", input: { x: 1 }, timeout_ms: 100, limits: { output_kb: 100 } });
  expect(result()).toEqual({ claim_token: "token", ok: true, value: "3", logs: "", stats: { ms: 1 } });
  expect(captureView).not.toHaveBeenCalled();
});
