// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { MemoryCard, MemoryDialog } from "../components/memory/memory-summary";
import { api } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../components/markdown", () => ({ default: ({ text }: { text: string }) => <p>{text}</p> }));
vi.mock("../components/library/modal", () => ({ default: ({ children, footer }: { children: React.ReactNode; footer?: React.ReactNode }) => <div>{children}{footer}</div> }));
const summary = { project_id: null, text: "摘要", entries: 1, stale: true, enabled: true, active: null };
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

test("disabled stale memory skips auto-run; enabled stale memory refreshes its scope once", async () => {
  vi.mocked(api).mockImplementation(async () => ({ ...summary, enabled: false }));
  const disabled = render(<MemoryDialog projectId={null} title="你的记忆" onClose={() => {}} />);
  await waitFor(() => assert.ok(screen.getByText("摘要")));
  assert.equal(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === "POST").length, 0);
  disabled.unmount();
  vi.mocked(api).mockClear();
  vi.mocked(api).mockImplementation(async (_path, options) => options?.method === "POST" ? { status: "nothing" } : summary);
  render(<MemoryDialog projectId="project-requested" title="项目记忆" onClose={() => {}} />);
  await waitFor(() => assert.equal(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === "POST").length, 1));
  assert.ok(vi.mocked(api).mock.calls.some(([path, options]) => path === "/v1/memory/dream/run?project_id=project-requested" && options?.method === "POST"));
  await act(async () => {});
  assert.equal(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === "POST").length, 1);
});

test("dialog and card do not poll the same scope twice", async () => {
  vi.mocked(api).mockResolvedValue({ ...summary, stale: false, active: { id: "dream", status: "running" } });
  render(<MemoryCard projectId={null} title="你的记忆" />);
  await waitFor(() => assert.ok(screen.getByLabelText("你的记忆")));
  await act(async () => {});
  vi.useFakeTimers();
  await act(async () => screen.getByLabelText("你的记忆").click());
  vi.mocked(api).mockClear();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.equal(vi.mocked(api).mock.calls.filter(([path]) => path === "/v1/memory/summary").length, 1);
});


test("edit reply and undo wait for the final summary lease to be released", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  let editing = false;
  let refreshing = true;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === "POST") { editing = true; return { id: "edited", status: "pending" }; }
    if (path === "/v1/memory/dreams/edited") return { id: "edited", status: "applied", report: { reply: "修改完成" } };
    return { ...summary, stale: false, text: editing && !refreshing ? "新摘要" : "旧摘要",
      active: editing && refreshing ? { id: "edited", status: "applied" } : null };
  });
  render(<MemoryDialog projectId={null} title="你的记忆" onClose={() => {}} />);
  await waitFor(() => assert.ok(screen.getByText("旧摘要")));
  vi.useFakeTimers();
  fireEvent.change(screen.getByLabelText("修改记忆"), { target: { value: "记住偏好" } });
  await act(async () => { fireEvent.submit(screen.getByLabelText("修改记忆").closest("form")!); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByLabelText("正在修改"));
  assert.equal(screen.queryByText("修改完成"), null);
  assert.equal(screen.queryByText("撤销"), null);
  refreshing = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByText("新摘要"));
  assert.ok(screen.getByText("修改完成"));
  assert.ok(screen.getByText("撤销"));
});
