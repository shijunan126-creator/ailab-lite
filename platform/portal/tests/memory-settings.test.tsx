// @vitest-environment jsdom
import assert from "node:assert/strict";
import { render, cleanup, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import MemoryPage from "../app/(app)/memory/page";
import { api } from "../lib/api";

vi.mock("../lib/api", () => ({ api: vi.fn(), errorMessage: String }));
vi.mock("../components/memory/memory-view", () => ({ MemoryPanel: () => null }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("switches fetch only the bounded settings response", async () => {
  vi.mocked(api).mockResolvedValue({ enabled: true, auto_save: true, reference_history: true, dream: { enabled: true } });
  render(<MemoryPage />);
  await waitFor(() => assert.equal(vi.mocked(api).mock.calls[0][0], "/v1/memory/settings"));
  assert.ok(!vi.mocked(api).mock.calls.some(([path]) => path === "/v1/memory"));
});
