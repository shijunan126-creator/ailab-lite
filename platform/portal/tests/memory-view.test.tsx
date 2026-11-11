// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { MemoryPanel, organizeNote, shortDate } from "../components/memory/memory-view";
import { api, type MemoryItem, type MemoryView } from "../lib/api";
import { confirmDialog } from "../lib/dialog";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));

const item = (id: string, content: string, extra: Partial<MemoryItem> = {}): MemoryItem => ({
  id, project_id: null, kind: "preference", content,
  observed_at: "2026-11-03T08:00:00Z", updated_at: "2026-11-03T08:00:00Z", ...extra,
});
const view: MemoryView = {
  project_id: null, enabled: true, memory_mode: null, merge_pending: false, revision: 7, active: null, used_chars: 40, max_chars: 6000,
  sections: [
    { kind: "preference", items: [item("mem_core", "回答先给结论")] },
    { kind: "project", items: [item("mem_proj", "在做 AILab 记忆模块", { kind: "project" })] },
    { kind: "interest", items: [item("mem_fin", "关注乐信季度业绩", { kind: "interest" })] },
  ],
};
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

test("dates and organize notes", () => {
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 0, changed: 2 } }), "看了 12 段对话，整理了 2 处记忆");
  const now = new Date("2026-11-09T00:00:00");
  assert.equal(shortDate("2026-11-03T08:00:00", now), "11-03");
  assert.equal(shortDate("2025-01-02T08:00:00", now), "2025-01-02");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 3 } }), "看了 12 段对话，新增 3 条");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 12, added: 0 } }), "看了 12 段对话，没有需要长期记住的内容");
  // a re-sort before the new conversations: additions and rewrites are both shown
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 1, added: 2, changed: 4 } }), "看了 1 段对话，新增 2 条，整理了 2 处记忆");
  assert.equal(organizeNote({ id: "d", status: "applied", report: { runs_seen: 0, added: 1, changed: 3 } }), "新增 1 条，整理了 2 处记忆");
});

test("shows the whole document by kind from one read", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  assert.ok(screen.getByText("关注乐信季度业绩"));
  // the sections the API sends, in its order; a kind without lines is a quiet card that only says so
  assert.deepEqual([...document.querySelectorAll(".memory-section-title")].map((h) => h.textContent), ["偏好1", "在做的事1", "关注的话题1"]);
  assert.deepEqual(vi.mocked(api).mock.calls.map(([path]) => path), ["/v1/memory/view"]);
  assert.equal(screen.queryByText(/快满了/), null);
});

test("lines of a kind are listed newest first", async () => {
  vi.mocked(api).mockResolvedValue({
    ...view,
    sections: [{ kind: "fact", items: [item("mem_a", "旧的", { observed_at: "2026-10-01T00:00:00Z" }), item("mem_b", "新的", { observed_at: "2026-11-01T00:00:00Z" })] }],
  });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("新的")));
  assert.deepEqual([...document.querySelectorAll(".memory-item-text")].map((n) => n.textContent), ["新的", "旧的"]);
});

test("every kind shows even when empty; a nearly full document says it will be condensed", async () => {
  const kinds = ["fact", "preference", "project", "interest", "reference"] as const;
  vi.mocked(api).mockResolvedValue({ ...view, sections: kinds.map((kind) => ({ kind, items: [] })) });
  const first = render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.equal(screen.getAllByText("暂无").length, 5));
  assert.deepEqual([...document.querySelectorAll(".memory-section.empty .memory-section-title")].map((h) => h.textContent), ["关于你", "偏好", "在做的事", "关注的话题", "资料"]);
  assert.equal(document.querySelectorAll(".memory-count").length, 0);
  assert.equal(document.querySelectorAll(".memory-section-title svg").length, 5);
  first.unmount();
  vi.mocked(api).mockResolvedValue({ ...view, used_chars: 5200 });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("快满了，下次整理会合并精简")));
});

test("every line is one kind: no tag, no confirm action; deleting one sends the revision it was read at", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("关注乐信季度业绩")));
  assert.equal(screen.queryByText("推断"), null);
  fireEvent.click(screen.getByLabelText("更多操作 关注乐信季度业绩"));
  assert.equal(screen.queryByText("确认无误"), null);
  await act(async () => { fireEvent.click(screen.getByText("删除")); });
  assert.equal(vi.mocked(confirmDialog).mock.calls.length, 1);
  await waitFor(() => assert.ok(vi.mocked(api).mock.calls.some(([path, o]) => path === "/v1/memory/items/mem_fin?expected_revision=7" && o?.method === "DELETE")));
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

test("a line shows only its date, never the conversations it was learned in", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, sections: [{ kind: "fact", items: [item("mem_q", "在券商做量化", { observed_at: null })] }] });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("在券商做量化")));
  const row = screen.getByText("在券商做量化").closest("li")!;
  assert.equal(row.querySelector(".memory-meta")!.textContent, "11-03");
  assert.equal(row.querySelector("a"), null);
});
