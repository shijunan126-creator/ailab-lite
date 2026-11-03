// @vitest-environment jsdom
// Its own file: the viewer asset cache is module state, and every other OfficeView test fills it.
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import OfficeView, { type OfficeDoc } from "../components/preview/office-view";
import { CHANNEL } from "../office/protocol";

vi.mock("../lib/api", () => ({ api: vi.fn().mockResolvedValue({ status: "ready", native: true, version: "hash1", bytes: 3 }), downloadUrl: (p: string) => p }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("viewer assets that cannot load fall back to the paged preview instead of a blank frame", async () => {
  vi.stubGlobal("React", React);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503 })));
  const doc: OfficeDoc = { path: "库/离线.pptx", filename: "离线.pptx", kind: "pptx", source: { fileId: "f1" } };
  render(<OfficeView doc={doc} docs={[doc]} onOpen={vi.fn()} onReference={vi.fn()} onAsk={vi.fn()} fallback={<div>fallback</div>} />);
  const frame = (await screen.findByLabelText(doc.filename)) as HTMLIFrameElement;
  expect(screen.getByRole("status", { name: "正在加载" })).toBeTruthy();
  await act(async () => {}); // the frame's listener is registered in an effect
  await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { ch: CHANNEL, t: "boot" } }));
  });
  expect(await screen.findByText("fallback")).toBeTruthy();
});
