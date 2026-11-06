// Messages between the Portal and the sandboxed office viewer frame (app/frame/office). The frame has an opaque
// origin, so both sides identify each other by window and the frame echoes the per-load nonce on every message.
// Shared by the Portal (components/preview/office-view.tsx) and the frame bundle (office/host.ts).

export const CHANNEL = "ailab-office";

export type Theme = "light" | "dark";

export interface HostContext {
  theme: Theme;
  locale: string;
  /** False where there is no conversation to quote into (library, shares): the selection bar stays hidden. */
  chat?: boolean;
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

/** The viewer draws no title bar of its own: the Portal's bar already names the file, so its two document controls live there. */
export type ViewerCommand = "drawer" | "find";
export type ViewerTools = Record<ViewerCommand, boolean | null>;

export type FrameMessage =
  | { ch: typeof CHANNEL; t: "boot" }
  | { ch: typeof CHANNEL; t: "error"; message: string }
  | { ch: typeof CHANNEL; t: "ready"; nonce: string; viewId: string }
  /** The viewer could not open or draw `version` (its load, prepare or renderer failed). Terminal for that version;
   * recoverable warnings are not sent. The Portal decides whether the source or the renderer was at fault. */
  | { ch: typeof CHANNEL; t: "failed"; nonce: string; version: string; stage: "load" | "render"; message: string; code?: string }
  | { ch: typeof CHANNEL; t: "call"; nonce: string; id: number; name: string; args: Record<string, unknown> }
  | { ch: typeof CHANNEL; t: "asset"; nonce: string; id: number; name: string; binary: boolean }
  | { ch: typeof CHANNEL; t: "bytes"; nonce: string; id: number; token: string }
  | { ch: typeof CHANNEL; t: "reference"; nonce: string; id: number; reference: ViewerReference }
  | { ch: typeof CHANNEL; t: "ask"; nonce: string; id: number; question: string; reference: ViewerReference }
  | { ch: typeof CHANNEL; t: "link"; nonce: string; url: string }
  /** The document controls the Portal shows in its own title bar: null when the control does not apply, else whether it is on. */
  | { ch: typeof CHANNEL; t: "tools"; nonce: string; tools: ViewerTools }
  | { ch: typeof CHANNEL; t: "page-tool-result"; nonce: string; id: number; ok: boolean; value?: unknown; error?: string };

export type HostMessage =
  | { ch: typeof CHANNEL; t: "bundle"; nonce: string; css: string; js: string }
  | { ch: typeof CHANNEL; t: "reply"; id: number; ok: true; value: unknown }
  | { ch: typeof CHANNEL; t: "reply"; id: number; ok: false; error: { message: string; code?: string } }
  | { ch: typeof CHANNEL; t: "context"; context: HostContext; recent: RecentDocument[]; open?: string }
  | { ch: typeof CHANNEL; t: "snapshot"; snapshot: unknown }
  | { ch: typeof CHANNEL; t: "page-tool"; id: number; name: string; args: Record<string, unknown> }
  | { ch: typeof CHANNEL; t: "page-tool-cancel"; id: number }
  | { ch: typeof CHANNEL; t: "dispose" }
  /** A press of one of those controls in the Portal's title bar. */
  | { ch: typeof CHANNEL; t: "command"; name: ViewerCommand };

/** Upper bound for a control message (file bytes travel as transferred ArrayBuffers). */
export const MAX_MESSAGE_CHARS = 256 * 1024;
/** PNG replies need room for an initial capture before the client-call handler shrinks it to its 2 MiB budget. */
export const MAX_CAPTURE_MESSAGE_CHARS = 16 * 1024 * 1024;

export function isChannel(data: unknown): data is { ch: typeof CHANNEL; t: string } {
  return !!data && typeof data === "object" && (data as { ch?: unknown }).ch === CHANNEL && typeof (data as { t?: unknown }).t === "string";
}
