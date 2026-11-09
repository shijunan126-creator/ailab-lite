// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import ProjectMemoryDialog from "../components/memory/project-memory";
import { api, type Project } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock("../lib/projects", () => ({ projectsChanged: vi.fn() }));
vi.mock("../components/memory/memory-view", () => ({
  MemoryDialog: ({ editable, top }: { editable: boolean; top: () => React.ReactNode }) => <div>{top()}{editable ? "可编辑" : "只读"}</div>,
}));
const project = { id: "project", memory_mode: "shared" } as Project;
afterEach(() => { cleanup(); vi.resetAllMocks(); });

test("shared project shows its memory read-only and switches to editable once isolated", async () => {
  render(<ProjectMemoryDialog project={project} onClose={() => {}} />);
  assert.ok(screen.getByText("只读"));
  vi.mocked(api).mockResolvedValue({ ...project, memory_mode: "isolated" });
  fireEvent.click(screen.getByRole("switch", { name: "仅使用本项目记忆" }));
  await waitFor(() => assert.ok(screen.getByText("可编辑")));
});

test("isolated project memory is editable", () => {
  render(<ProjectMemoryDialog project={{ ...project, memory_mode: "isolated" }} onClose={() => {}} />);
  assert.ok(screen.getByText("可编辑"));
});
