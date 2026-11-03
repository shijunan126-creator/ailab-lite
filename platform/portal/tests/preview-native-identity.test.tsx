// @vitest-environment jsdom
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
vi.mock("../components/preview/office-view", () => ({
  officeKind: (name: string) => name.endsWith(".docx") ? "docx" : null,
  default: ({ doc, revision }: { doc: { filename: string }; revision: number }) => <iframe title={doc.filename} data-revision={revision} />,
}));
vi.mock("../components/preview/pdf", () => ({ default: () => null }));
vi.mock("../components/preview/file-viewer", () => ({ PagedPreview: () => null }));
import PreviewPane from "../components/chat/preview-pane";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("native pane keeps its frame across derived preview revisions, but replaces a different source", () => {
  vi.stubGlobal("React", React);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  const preview = { filename: "draft.docx", path: "outputs/draft.docx", run_id: "r", version: 0, kind: "pages" as const, pages: [] };
  const props = {
    previews: [preview], selected: "draft.docx", onSelect: vi.fn(), onCloseFile: vi.fn(), sources: null, sourcesOn: false,
    onSourcesTab: vi.fn(), onCloseSources: vi.fn(), readers: [], readerUrl: null, readerOn: false, onReaderTab: vi.fn(),
    onCloseReader: vi.fn(), onRead: vi.fn(), onClose: vi.fn(), live: true, downloads: {},
    onReference: vi.fn(), onAsk: vi.fn().mockResolvedValue(undefined),
  };
  const view = render(<PreviewPane {...props} />);
  const frame = view.container.querySelector("iframe")!;
  view.rerender(<PreviewPane {...props} previews={[{ ...preview, version: 1, pages: ["derived.png"] }]} />);
  expect(view.container.querySelector("iframe")!).toBe(frame);
  expect(frame.getAttribute("data-revision")).toBe("1");
  view.rerender(<PreviewPane {...props} previews={[{ ...preview, run_id: "another" }]} />);
  expect(view.container.querySelector("iframe")!).not.toBe(frame);
});
