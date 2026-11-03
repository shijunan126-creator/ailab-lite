// @vitest-environment jsdom
// A question asked in the native viewer, through the real side pane: PreviewPane picks the document's identity
// (library file, delivered file or Work workspace path), OfficeView freezes it with the version the user saw,
// and the pane's onAsk (Chat's outbox submit with docRefs) gets exactly that.
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import PreviewPane, { type PanePreview } from "../components/chat/preview-pane";
import { api } from "../lib/api";
import { frameBridge } from "./office-bridge";

vi.mock("../lib/api", async (original) => ({ ...(await original<typeof import("../lib/api")>()), api: vi.fn() }));
vi.mock("../components/preview/pdf", () => ({ default: () => null }));
vi.mock("../components/preview/file-viewer", () => ({ FilePreviewBody: () => null }));
const request = vi.mocked(api);

beforeEach(() => {
  vi.stubGlobal("React", React);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).endsWith("manifest.json") ? { ok: true, json: async () => ({ version: "v" }) } : { ok: true, text: async () => "asset" })));
  request.mockReset().mockImplementation(async (url: string) => ({ status: "ready", native: true, version: `sha-${url}`, bytes: 10 }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const KINDS = [["docx", "p:3"], ["xlsx", "Revenue!A1:B2"], ["pptx", "slide:1/shape:2"], ["pdf", "page:1"]] as const;
const SOURCES = ["library", "delivered", "workspace"] as const;

function pane(kind: string, source: (typeof SOURCES)[number], onAsk: (question: string, ref: unknown, label: string) => Promise<void>) {
  const filename = `季度.${kind}`;
  const preview: PanePreview =
    source === "library"
      ? { filename, libraryFileId: "lib1", version: 0, kind: "pages", pages: [] } as unknown as PanePreview
      : { filename, run_id: "run1", path: `/workspace/outputs/${filename}`, version: 0, kind: "pages", pages: [] } as unknown as PanePreview;
  const key = source === "library" ? "file:lib1" : filename;
  render(
    <PreviewPane
      previews={[preview]} selected={key} onSelect={vi.fn()} onCloseFile={vi.fn()} sources={null} sourcesOn={false}
      onSourcesTab={vi.fn()} onCloseSources={vi.fn()} readers={[]} readerUrl={null} readerOn={false} onReaderTab={vi.fn()}
      onCloseReader={vi.fn()} onRead={vi.fn()} onClose={vi.fn()} live={false} downloads={{}} conversationId="c1"
      delivered={source === "delivered" ? { [filename]: "delivered1" } : {}} onReference={vi.fn()} onAsk={onAsk}
    />,
  );
  return filename;
}

for (const [kind, at] of KINDS)
  for (const source of SOURCES)
    test(`${kind} ${source}: asking about a selection sends it with the exact document version`, async () => {
      const onAsk = vi.fn().mockResolvedValue(undefined);
      const filename = pane(kind, source, onAsk);
      const b = await frameBridge(filename);
      await b.send({ t: "ready", viewId: `${kind}-${source}` });
      const path = source === "workspace" ? `工作区/outputs/${filename}` : `库/${filename}`;
      await b.send({ t: "ask", id: 7, question: "这里的数字怎么来的？", reference: { path, at, text: "所选内容" } });
      const identity =
        source === "workspace"
          ? { run_id: "run1", path: `outputs/${filename}`, version: `sha-/v1/runs/run1/workspace/document?path=${encodeURIComponent(`outputs/${filename}`)}` }
          : { file_id: source === "library" ? "lib1" : "delivered1", version: `sha-/v1/files/${source === "library" ? "lib1" : "delivered1"}/document` };
      expect(onAsk).toHaveBeenCalledWith("这里的数字怎么来的？", { ...identity, at, quote: "所选内容" }, `${filename} · ${at}`);
      expect(b.reply(7)).toMatchObject({ ok: true });
    });

test("a question that could not be sent tells the viewer, which keeps the selection for another try", async () => {
  const onAsk = vi.fn().mockRejectedValue(new Error("额度已用完"));
  const filename = pane("docx", "library", onAsk);
  const b = await frameBridge(filename);
  await b.send({ t: "ready", viewId: "failing" });
  await b.send({ t: "ask", id: 1, question: "解释", reference: { path: `库/${filename}`, at: "p:1", text: "句子" } });
  expect(b.reply(1)).toMatchObject({ ok: false, error: { message: "额度已用完" } });
});

test("a question about another document than the one shown is refused, not sent", async () => {
  const onAsk = vi.fn().mockResolvedValue(undefined);
  const filename = pane("xlsx", "library", onAsk);
  const b = await frameBridge(filename);
  await b.send({ t: "ask", id: 1, question: "解释", reference: { path: "库/别的.xlsx", at: "A1", text: "1" } });
  expect(onAsk).not.toHaveBeenCalled();
  expect(b.reply(1)).toMatchObject({ ok: false });
});
