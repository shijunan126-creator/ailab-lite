// @vitest-environment jsdom
import assert from "node:assert/strict";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { FilePreviewBody } from "../components/preview/file-viewer";
import type { FileItem } from "../lib/api";

afterEach(() => vi.unstubAllGlobals());

test("a scanned file shows its first published page while later pages are pending", async () => {
  vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    status: "pending", kind: "pages", pages: ["p-1.jpg"], page_count: 3,
  }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const file: FileItem = {
    id: "file", filename: "deck.pdf", content_type: "application/pdf", size: 10, status: "pending",
    version: 1, meta: { scan: { verdict: "clean" } }, created_at: "2026-10-31T00:00:00Z", download_url: "",
  };
  const { unmount } = render(<FilePreviewBody file={file} />);
  await waitFor(() => assert.ok(screen.getByAltText("第 1 页")));
  assert.equal(screen.queryByText(/下载查看全部/), null);
  unmount();
});
