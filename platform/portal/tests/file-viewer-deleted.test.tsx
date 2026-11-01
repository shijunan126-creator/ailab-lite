// @vitest-environment jsdom
import assert from "node:assert/strict";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import { useFileViewer } from "../components/preview/file-viewer";

afterEach(() => vi.unstubAllGlobals());

function Opener({ id }: { id: string }) {
  const files = useFileViewer();
  return (
    <>
      <button onClick={() => files.open([id], id)}>open</button>
      {files.viewer}
    </>
  );
}

test("opening a file the library no longer lists says it was deleted instead of doing nothing", async () => {
  // trashed and purged files are left out of the ids lookup
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })));
  const { unmount } = render(<Opener id="gone" />);
  await act(async () => screen.getByText("open").click());
  await waitFor(() => assert.ok(screen.getByText("文件已删除")));
  await act(async () => screen.getByLabelText("关闭预览").click());
  assert.equal(screen.queryByText("文件已删除"), null);
  unmount();
});
