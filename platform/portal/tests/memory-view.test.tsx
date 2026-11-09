// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { MemoryPanel, organizeNote, shortDate } from "../components/memory/memory-view";
import { api, type MemoryItem, type MemoryView } from "../lib/api";
import { confirmDialog } from "../lib/dialog";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

const item = (id: string, content: string, extra: Partial<MemoryItem> = {}): MemoryItem => ({
  id, project_id: null, kind: "preference", origin: "explicit", tier: "archive", page: "偏好", content, status: "active",
  observed_at: "2026-11-03T08:00:00Z", valid_from: null, valid_to: null, expires_at: null, updated_at: "2026-11-03T08:00:00Z",
  support_conversations: 1, sources: [{ run_id: "run_1", conversation_id: "conv_1", conversation_title: "周报", date: "2026-11-03" }], ...extra,
});
const view: MemoryView = {
  project_id: null, enabled: true, memory_mode: null, merge_pending: false, revision: 7, active: null, total: 4,
  core: [item("mem_core", "回答先给结论", { tier: "core" })],
  review: [],
  sections: [
    { kind: "project", count: 1, items: [item("mem_proj", "在做 AILab 记忆模块", { kind: "project", page: "项目/AILab" })], groups: [] },
    {
      kind: "interest", count: 2, items: [],
      groups: [{ name: "金融科技", count: 2, preview: ["乐信", "英伟达"] }],
    },
  ],
};
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

test("dates and organize notes", () => {
  const now = new Date("2026-11-09T00:00:00");
  assert.equal(shortDate("2026-11-03T08:00:00", now), "11-03");
  assert.equal(shortDate("2025-01-02T08:00:00", now), "2025-01-02");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 3 } }), "看了 12 段对话，新增 3 条");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 0 } }), "看了 12 段对话，没有需要长期记住的内容");
});

test("lays memory out as what is always used, then what is looked up by kind, with the source conversation", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  assert.ok(screen.getByText("每次对话都记着"));
  assert.ok(screen.getByText("在做的事"));
  assert.ok(screen.getByText("关注的话题"));
  assert.ok(screen.getByText("乐信、英伟达"));
  assert.equal(screen.getAllByText("周报")[0].closest("a")?.getAttribute("href"), "/c/conv_1");
});

test("an empty core says how to fill it; nothing at all says where memory comes from", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, core: [] });
  const first = render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText(/还没有。说一句/)));
  first.unmount();
  vi.mocked(api).mockResolvedValue({ ...view, total: 0, core: [], sections: [] });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText(/AI 会在对话中和每晚整理时记下/)));
});

test("a topic loads its items on demand; deleting one sends the revision it was read at", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    String(path).startsWith("/v1/memory/items?")
      ? { items: [item("mem_fin", "关注乐信季度业绩", { kind: "interest", page: "兴趣/金融科技", origin: "inferred" })], next_cursor: null }
      : view
  );
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("金融科技")));
  assert.equal(vi.mocked(api).mock.calls.filter(([path]) => String(path).startsWith("/v1/memory/items")).length, 0);
  fireEvent.click(screen.getByText("金融科技"));
  await waitFor(() => assert.ok(screen.getByText("关注乐信季度业绩")));
  const call = vi.mocked(api).mock.calls.find(([path]) => String(path).startsWith("/v1/memory/items"))!;
  const q = new URLSearchParams(String(call[0]).split("?")[1]);
  assert.deepEqual([q.get("kind"), q.get("group"), q.get("tier")], ["interest", "金融科技", "archive"]);
  assert.ok(screen.getByText("推断"));
  fireEvent.click(screen.getByLabelText("更多操作 关注乐信季度业绩"));
  await act(async () => { fireEvent.click(screen.getByText("删除")); });
  assert.equal(vi.mocked(confirmDialog).mock.calls.length, 1);
  await waitFor(() => assert.ok(vi.mocked(api).mock.calls.some(([path, o]) => path === "/v1/memory/items/mem_fin?expected_revision=7" && o?.method === "DELETE")));
});

test("a sensitive item waits for confirmation and is confirmed in place", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, review: [item("mem_rev", "做过一次手术", { status: "review" })] });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText(/做过一次手术/)));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "确认" })); });
  const patch = vi.mocked(api).mock.calls.find(([path, o]) => path === "/v1/memory/items/mem_rev" && o?.method === "PATCH");
  assert.deepEqual(JSON.parse(patch![1]!.body as string), { expected_revision: 7, status: "active" });
});

test("立即整理 follows its own run in the requested scope and says what it did", async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === "POST") return { id: "dream_1", status: "pending" };
    if (path === "/v1/memory/dreams/dream_1") return { id: "dream_1", status: "applied", report: { runs_seen: 20, added: 0 } };
    return { ...view, project_id: "p1" };
  });
  render(<MemoryPanel projectId="p1" title="项目记忆" heading="none" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(screen.getByText("立即整理")); });
  assert.ok(vi.mocked(api).mock.calls.some(([path, o]) => path === "/v1/memory/dream/run?project_id=p1" && o?.method === "POST"));
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  assert.ok(screen.getByText("看了 20 段对话，没有需要长期记住的内容"));
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
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
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
  render(<MemoryPanel projectId="p1" title="项目记忆" heading="none" editable={false} />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  assert.equal(screen.queryByLabelText("修改记忆"), null);
  assert.equal(screen.queryByLabelText("更多操作 回答先给结论"), null);
});

test("an item is edited in place and saved with the revision it was read at", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  fireEvent.click(screen.getByLabelText("更多操作 回答先给结论"));
  fireEvent.click(screen.getByText("编辑"));
  fireEvent.change(screen.getByLabelText("编辑记忆"), { target: { value: "回答先给结论，再给依据" } });
  await act(async () => { fireEvent.keyDown(screen.getByLabelText("编辑记忆"), { key: "Enter" }); });
  const patch = vi.mocked(api).mock.calls.find(([path, o]) => path === "/v1/memory/items/mem_core" && o?.method === "PATCH");
  assert.deepEqual(JSON.parse(patch![1]!.body as string), { expected_revision: 7, content: "回答先给结论，再给依据" });
  await waitFor(() => assert.equal(screen.queryByLabelText("编辑记忆"), null));
});

test("search appears once there is enough to search, and drops answers to an older query", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, total: 3 });
  const small = render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  assert.equal(screen.queryByLabelText("搜索记忆"), null);
  small.unmount();
  vi.mocked(api).mockImplementation(async (path) => {
    const q = new URLSearchParams(String(path).split("?")[1] || "").get("q");
    if (q === "乐信") return { items: [item("mem_fin", "关注乐信季度业绩", { kind: "interest" })], next_cursor: null };
    if (q) return { items: [], next_cursor: null };
    return { ...view, total: 30 };
  });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByLabelText("搜索记忆")));
  vi.useFakeTimers();
  fireEvent.change(screen.getByLabelText("搜索记忆"), { target: { value: "乐" } });
  fireEvent.change(screen.getByLabelText("搜索记忆"), { target: { value: "乐信" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  assert.ok(screen.getByText("关注乐信季度业绩"));
  assert.equal(screen.queryByText("每次对话都记着"), null);
  assert.equal(vi.mocked(api).mock.calls.filter(([path]) => String(path).includes("q=")).length, 1); // the first keystroke never left
  fireEvent.change(screen.getByLabelText("搜索记忆"), { target: { value: "无关" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  assert.ok(screen.getByText("没有找到相关的记忆"));
});
