// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import ProjectMemoryDialog from "../components/memory/project-memory";
import { api, type Project } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock("../lib/projects", () => ({ projectsChanged: vi.fn() }));
vi.mock("../components/memory/memory-summary", () => ({ MemoryDialog: () => <div>隔离摘要与编辑器</div> }));
const project = { id: "project", memory_mode: "shared" } as Project;
afterEach(() => { cleanup(); vi.resetAllMocks(); });

test("shared project exposes only isolation setting and never loads summaries", async () => {
  render(<ProjectMemoryDialog project={project} onClose={() => {}} />);
  assert.equal(screen.queryByText("隔离摘要与编辑器"), null);
  assert.ok(screen.getByRole("switch", { name: "仅使用本项目记忆" }));
  assert.equal(vi.mocked(api).mock.calls.length, 0);
  vi.mocked(api).mockResolvedValue({ ...project, memory_mode: "isolated" });
  fireEvent.click(screen.getByRole("switch", { name: "仅使用本项目记忆" }));
  await waitFor(() => assert.ok(screen.getByText("隔离摘要与编辑器")));
});

test("isolated project exposes its summary and editor", () => {
  render(<ProjectMemoryDialog project={{ ...project, memory_mode: "isolated" }} onClose={() => {}} />);
  assert.ok(screen.getByText("隔离摘要与编辑器"));
});
