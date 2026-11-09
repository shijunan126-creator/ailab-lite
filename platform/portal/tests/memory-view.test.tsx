// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { MemoryCard, MemoryDialog, organizeNote, shortDate } from "../components/memory/memory-view";
import { api, type MemoryItem } from "../lib/api";
import { confirmDialog } from "../lib/dialog";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("../components/library/modal", () => ({
  default: ({ children, footer, actions }: { children: React.ReactNode; footer?: React.ReactNode; actions?: React.ReactNode }) => <div>{actions}{children}{footer}</div>,
}));

const item = (id: string, content: string, extra: Partial<MemoryItem> = {}): MemoryItem => ({
  id, project_id: null, kind: "preference", origin: "explicit", tier: "core", page: "偏好", content, status: "active",
  observed_at: "2026-11-03T08:00:00Z", valid_from: null, valid_to: null, expires_at: null, updated_at: "2026-11-03T08:00:00Z",
  sources: [{ run_id: "run_1", conversation_id: "conv_1", conversation_title: "周报", date: "2026-11-03" }], ...extra,
});
const view = {
  project_id: null, enabled: true, memory_mode: null, merge_pending: false, revision: 7, active: null,
  core: [item("mem_core", "回答用中文")], pages: [{ page: "兴趣/金融科技", count: 1, updated_at: null }],
};
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

test("dates and organize notes", () => {
  const now = new Date("2026-11-09T00:00:00");
  assert.equal(shortDate("2026-11-03T08:00:00", now), "11-03");
  assert.equal(shortDate("2025-01-02T08:00:00", now), "2025-01-02");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 3 } }), "看了 12 段对话，新增 3 条");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 0 } }), "看了 12 段对话，没有需要长期记住的内容");
});

test("dialog and card do not poll the same scope twice", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, active: { id: "dream", status: "running", trigger: "cron" } });
  render(<MemoryCard projectId={null} title="你的记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答用中文")));
  vi.useFakeTimers();
  await act(async () => screen.getByLabelText("你的记忆").click());
  vi.mocked(api).mockClear();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.equal(vi.mocked(api).mock.calls.filter(([path]) => path === "/v1/memory/view").length, 1);
});

test("立即整理 follows its own run in the requested scope and says what it did", async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === "POST") return { id: "dream_1", status: "pending" };
    if (path === "/v1/memory/dreams/dream_1") return { id: "dream_1", status: "applied", report: { runs_seen: 20, added: 0 } };
    return { ...view, project_id: "p1" };
  });
  render(<MemoryDialog projectId="p1" title="项目记忆" onClose={() => {}} />);
  await waitFor(() => assert.ok(screen.getByText("回答用中文")));
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(screen.getByText("立即整理")); });
  assert.ok(vi.mocked(api).mock.calls.some(([path, o]) => path === "/v1/memory/dream/run?project_id=p1" && o?.method === "POST"));
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByText("看了 20 段对话，没有需要长期记住的内容"));
});

test("a topic page loads on demand; deleting an item sends the revision it was read at", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    String(path).startsWith("/v1/memory/items?") ? { items: [item("mem_fin", "关注乐信业绩（11-03）", { tier: "archive", kind: "interest", origin: "inferred" })], next_cursor: null } : view
  );
  render(<MemoryDialog projectId={null} title="你的记忆" onClose={() => {}} />);
  await waitFor(() => assert.ok(screen.getByText("兴趣/金融科技")));
  assert.equal(vi.mocked(api).mock.calls.filter(([path]) => String(path).startsWith("/v1/memory/items")).length, 0);
  fireEvent.click(screen.getByText("兴趣/金融科技"));
  await waitFor(() => assert.ok(screen.getByText("关注乐信业绩（11-03）")));
  assert.ok(screen.getByText("推断"));
  fireEvent.click(screen.getByText("关注乐信业绩（11-03）"));
  assert.equal(screen.getByText("周报").closest("a")?.getAttribute("href"), "/c/conv_1");
  fireEvent.click(screen.getByLabelText("更多操作 关注乐信业绩（11-03）"));
  await act(async () => { fireEvent.click(screen.getByText("删除")); });
  assert.equal(vi.mocked(confirmDialog).mock.calls.length, 1);
  await waitFor(() => assert.ok(vi.mocked(api).mock.calls.some(([path, o]) => path === "/v1/memory/items/mem_fin?expected_revision=7" && o?.method === "DELETE")));
});

test("edit sends the revision, then replies and offers undo only after the lease is released", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  let editing = false;
  let refreshing = true;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === "POST") { editing = true; return { id: "edited", status: "pending" }; }
    if (path === "/v1/memory/dreams/edited") return { id: "edited", status: "applied", report: { reply: "修改完成" } };
    return { ...view, active: editing && refreshing ? { id: "edited", status: "applied", trigger: "edit" } : null };
  });
  render(<MemoryDialog projectId={null} title="你的记忆" onClose={() => {}} />);
  await waitFor(() => assert.ok(screen.getByText("回答用中文")));
  vi.useFakeTimers();
  fireEvent.change(screen.getByLabelText("修改记忆"), { target: { value: "记住偏好" } });
  await act(async () => { fireEvent.submit(screen.getByLabelText("修改记忆").closest("form")!); });
  const call = vi.mocked(api).mock.calls.find(([path]) => path === "/v1/memory/edit");
  assert.deepEqual(JSON.parse(call![1]!.body as string), { project_id: null, instruction: "记住偏好", expected_revision: 7 });
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByLabelText("正在修改"));
  assert.equal(screen.queryByText("撤销"), null);
  refreshing = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByText("修改完成"));
  assert.ok(screen.getByText("撤销"));
});

test("a shared project's memory is read-only", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, project_id: "p1", memory_mode: "shared" });
  render(<MemoryDialog projectId="p1" title="项目记忆" onClose={() => {}} editable={false} />);
  await waitFor(() => assert.ok(screen.getByText("回答用中文")));
  assert.equal(screen.queryByLabelText("修改记忆"), null);
  assert.ok((screen.getByLabelText("更多操作 回答用中文") as HTMLButtonElement).disabled);
});
