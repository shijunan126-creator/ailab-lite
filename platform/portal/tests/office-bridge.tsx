// Drives the Portal side of the office viewer frame the way the frame bundle does: by postMessage from the
// frame's window. Shared by the OfficeView and side-pane tests (not a test file itself).
import { act, screen, waitFor } from "@testing-library/react";
import { expect, vi } from "vitest";
import { CHANNEL } from "../office/protocol";

export async function frameBridge(title: string) {
  // the pane's tab carries the same title; the frame is the iframe
  const frame = await waitFor(() => {
    const node = screen.getAllByTitle(title).find((element): element is HTMLIFrameElement => element instanceof HTMLIFrameElement);
    expect(node).toBeTruthy();
    return node!;
  });
  await act(async () => {});
  const post = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
  const emit = async (data: object, source: Window | null = frame.contentWindow) => {
    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", { source, data: { ch: CHANNEL, ...data } }));
    });
  };
  await emit({ t: "boot" });
  await waitFor(() => expect(post.mock.calls.some(([m]) => m.t === "bundle")).toBe(true));
  const bundle = post.mock.calls.find(([m]) => m.t === "bundle")![0];
  const nonce = bundle.nonce;
  const send = (data: object) => emit({ nonce, ...data });
  const reply = (id: number) => post.mock.calls.map(([m]) => m).find((m) => m.t === "reply" && m.id === id);
  post.mockClear();
  return { frame, post, send, emit, reply, bundle };
}
