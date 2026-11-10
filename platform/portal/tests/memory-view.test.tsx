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
  id, project_id: null, kind: "preference", origin: "explicit", content, status: "active",
  observed_at: "2026-11-03T08:00:00Z", updated_at: "2026-11-03T08:00:00Z",
  sources: [{ run_id: "run_1", conversation_id: "conv_1", conversation_title: "周报", date: "2026-11-03" }], ...extra,
});
const view: MemoryView = {
  project_id: null, enabled: true, memory_mode: null, merge_pending: false, revision: 7, active: null, used_chars: 40, max_chars: 6000,
  review: [],
  sections: [
    { kind: "preference", items: [item("mem_core", "回答先给结论")] },
    { kind: "project", items: [item("mem_proj", "在做 AILab 记忆模块", { kind: "project" })] },
    { kind: "interest", items: [item("mem_fin", "关注乐信季度业绩", { kind: "interest", origin: "inferred" })] },
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
});

test("shows the whole document by kind, each line with its source conversation, from one read", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("回答先给结论")));
  for (const title of ["偏好", "在做的事", "关注的话题"]) assert.ok(screen.getByText(title));
  assert.ok(screen.getByText("关注乐信季度业绩"));
  assert.equal(screen.queryByText("关于你"), null); // an empty kind is not shown
  assert.equal(screen.getAllByText("周报")[0].closest("a")?.getAttribute("href"), "/c/conv_1");
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

test("nothing at all says where memory comes from; a nearly full document says it will be condensed", async () => {
  vi.mocked(api).mockResolvedValue({ ...view, sections: [] });
  const first = render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText(/每晚整理时记下/)));
  first.unmount();
  vi.mocked(api).mockResolvedValue({ ...view, used_chars: 5200 });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("快满了，下次整理会合并精简")));
});

test("an inferred line is marked and can be confirmed; deleting one sends the revision it was read at", async () => {
  vi.mocked(api).mockResolvedValue(view);
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("关注乐信季度业绩")));
  assert.equal(screen.getAllByText("推断").length, 1);
  fireEvent.click(screen.getByLabelText("更多操作 关注乐信季度业绩"));
  await act(async () => { fireEvent.click(screen.getByText("确认无误")); });
  const confirm = vi.mocked(api).mock.calls.find(([path, o]) => path === "/v1/memory/items/mem_fin" && o?.method === "PATCH");
  assert.deepEqual(JSON.parse(confirm![1]!.body as string), { expected_revision: 7, origin: "explicit" });
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

test("a line without a source shows only its date; only an edited one says the user added it", async () => {
  vi.mocked(api).mockResolvedValue({
    ...view,
    sections: [
      {
        kind: "fact",
        items: [
          item("mem_gone", "在券商做量化", { origin: "inferred", sources: [] }),
          item("mem_said", "常用 Python", { origin: "explicit", sources: [] }),
          item("mem_typed", "住在上海", { origin: "edited", sources: [] }),
        ],
      },
    ],
  });
  render(<MemoryPanel projectId={null} title="记忆" />);
  await waitFor(() => assert.ok(screen.getByText("在券商做量化")));
  const meta = (text: string) => screen.getByText(text).closest("li")!.querySelector(".memory-meta")!.textContent;
  assert.equal(meta("在券商做量化"), "11-03");
  assert.equal(meta("常用 Python"), "11-03");
  assert.equal(meta("住在上海"), "你添加的11-03");
});
