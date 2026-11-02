// Messages between the Portal and the sandboxed office viewer frame (app/frame/office). The frame has an opaque
// origin, so both sides identify each other by window and the frame echoes the per-load nonce on every message.
// Shared by the Portal (components/preview/office-view.tsx) and the frame bundle (office/host.ts).

export const CHANNEL = "ailab-office";

export type Theme = "light" | "dark";

export interface HostContext {
  theme: Theme;
  locale: string;
}

/** A reference the user made in the viewer: `path` is the viewer's document key, `at` its locator. */
export interface ViewerReference {
  path: string;
  at: string;
  text: string;
}

export interface RecentDocument {
  path: string;
  kind: "docx" | "xlsx" | "pptx" | "pdf";
}

export type FrameMessage =
  | { ch: typeof CHANNEL; t: "boot" }
  | { ch: typeof CHANNEL; t: "error"; message: string }
  | { ch: typeof CHANNEL; t: "ready"; nonce: string; viewId: string }
  | { ch: typeof CHANNEL; t: "call"; nonce: string; id: number; name: string; args: Record<string, unknown> }
  | { ch: typeof CHANNEL; t: "asset"; nonce: string; id: number; name: string; binary: boolean }
  | { ch: typeof CHANNEL; t: "bytes"; nonce: string; id: number; token: string }
  | { ch: typeof CHANNEL; t: "reference"; nonce: string; id: number; reference: ViewerReference }
  | { ch: typeof CHANNEL; t: "ask"; nonce: string; id: number; question: string; reference: ViewerReference }
  | { ch: typeof CHANNEL; t: "link"; nonce: string; url: string }
  | { ch: typeof CHANNEL; t: "page-tool-result"; nonce: string; id: number; ok: boolean; value?: unknown; error?: string };

export type HostMessage =
  | { ch: typeof CHANNEL; t: "bundle"; nonce: string; css: string; js: string }
  | { ch: typeof CHANNEL; t: "reply"; id: number; ok: true; value: unknown }
  | { ch: typeof CHANNEL; t: "reply"; id: number; ok: false; error: { message: string; code?: string } }
  | { ch: typeof CHANNEL; t: "context"; context: HostContext; recent: RecentDocument[]; open?: string }
  | { ch: typeof CHANNEL; t: "snapshot"; snapshot: unknown }
  | { ch: typeof CHANNEL; t: "page-tool"; id: number; name: string; args: Record<string, unknown> };

/** Upper bound for one structured message (bytes travel as transferred ArrayBuffers, not inside this). */
export const MAX_MESSAGE_CHARS = 256 * 1024;

export function isChannel(data: unknown): data is { ch: typeof CHANNEL; t: string } {
  return !!data && typeof data === "object" && (data as { ch?: unknown }).ch === CHANNEL && typeof (data as { t?: unknown }).t === "string";
}
