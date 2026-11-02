// AILab replacement for the runtime's ui/host.ts. Upstream connects an MCP Apps `App` to its desktop host; here the
// viewer runs in an opaque-origin frame of the Portal and talks to it over postMessage (office/protocol.ts). The
// object keeps the upstream Host surface so the shell, viewers and hooks run unchanged; only transport differs.
import { CHANNEL, isChannel, type HostContext, type HostMessage, type RecentDocument, type ViewerReference } from "./protocol";

type Json = Record<string, unknown>;
export type CallToolResult = {
  isError?: boolean;
  content?: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[];
  structuredContent?: unknown;
  _meta?: Json;
};
export type PageToolHandler = (args: Json, signal: AbortSignal | undefined) => Promise<CallToolResult>;
type ToolResult = { isError?: boolean; structuredContent?: unknown; _meta?: Json };

/** "page:3" from "库/report.pdf#page:3"; the viewer's anchors are `<path>#<locator>`. */
function splitAnchor(anchor: string): { path: string; at: string } {
  const hash = anchor.lastIndexOf("#");
  return hash < 0 ? { path: anchor, at: "" } : { path: anchor.slice(0, hash), at: anchor.slice(hash + 1) };
}

function createHost() {
  const nonce = (window as unknown as { __officeNonce?: string }).__officeNonce ?? "";
  const viewId = crypto.randomUUID();
  const pending = new Map<number, { resolve(value: unknown): void; reject(error: Error & { code?: string }): void }>();
  const handlers = new Map<string, PageToolHandler>();
  const waiters = new Set<() => void>();
  const results: ToolResult[] = [];
  let context: HostContext = { theme: "light", locale: "zh-CN" };
  let recent: RecentDocument[] = [];
  let opened: string | null = null;
  let sequence = 0;
  let resolveReady!: () => void;
  const readyPromise = new Promise<void>((resolve) => (resolveReady = resolve));
  const emit = (name: string) => void window.dispatchEvent(new Event(`office:${name}`));
  const post = (message: Json, transfer: Transferable[] = []) => parent.postMessage({ ch: CHANNEL, nonce, ...message }, "*", transfer);

  function request<T>(message: Json): Promise<T> {
    const id = ++sequence;
    return new Promise<T>((resolve, reject) => {
      pending.set(id, { resolve: resolve as (value: unknown) => void, reject });
      post({ ...message, id });
    });
  }

  async function runPageTool(id: number, name: string, args: Json) {
    try {
      // The bundle registers its page tool on mount; a request can arrive while it is still loading.
      const deadline = Date.now() + 20_000;
      while (!handlers.has(name) && Date.now() < deadline)
        await new Promise<void>((resolve) => {
          const done = () => (waiters.delete(done), resolve());
          waiters.add(done);
          setTimeout(done, 500);
        });
      const handler = handlers.get(name);
      if (!handler) throw new Error("查看器仍在加载");
      post({ t: "page-tool-result", id, ok: true, value: await handler(args, undefined) });
    } catch (error) {
      post({ t: "page-tool-result", id, ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }

  window.addEventListener("message", (event) => {
    if (event.source !== parent || !isChannel(event.data)) return;
    const message = event.data as HostMessage;
    if (message.t === "reply") {
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      if ("error" in message) waiter.reject(Object.assign(new Error(message.error.message), { code: message.error.code }));
      else waiter.resolve(message.value);
    } else if (message.t === "context") {
      context = message.context;
      recent = message.recent;
      if (message.open) opened = message.open;
      resolveReady();
      emit("hostchange");
    } else if (message.t === "snapshot") {
      results.push({ structuredContent: message.snapshot });
      if (results.length > 20) results.shift();
      emit("toolresult");
    } else if (message.t === "page-tool") void runPageTool(message.id, message.name, message.args);
  });

  // The Portal answers with the theme, the conversation's documents and which one to open.
  post({ t: "ready", viewId });

  const app = {
    async callServerTool({ name, arguments: args }: { name: string; arguments?: Json }): Promise<CallToolResult> {
      try {
        return { structuredContent: await request({ t: "call", name, args: args ?? {} }) };
      } catch (error) {
        const failure = error as Error & { code?: string };
        return {
          isError: true,
          content: [{ type: "text", text: failure.message }],
          structuredContent: { error: { message: failure.message, code: failure.code } },
        };
      }
    },
    /** Add to chat: the shell sends every reference so far; the newest is the one just made. */
    async updateModelContext({ structuredContent }: { structuredContent?: unknown }) {
      const references = (structuredContent as { references?: { anchor: string; text: string }[] } | undefined)?.references ?? [];
      const last = references[references.length - 1];
      if (!last) return;
      const { path, at } = splitAnchor(last.anchor);
      await request({ t: "reference", reference: { path, at, text: last.text } satisfies ViewerReference });
    },
    /** Ask about a selection. The adapted shell passes the selection itself (`ailabRef`), not only prose. */
    async sendMessage({ ailabRef }: { ailabRef?: { question: string; anchor: string; text: string } }) {
      if (!ailabRef) return { isError: true };
      const { path, at } = splitAnchor(ailabRef.anchor);
      await request({ t: "ask", question: ailabRef.question, reference: { path, at, text: ailabRef.text } satisfies ViewerReference });
      return { isError: false };
    },
    async openLink({ url }: { url: string }) {
      post({ t: "link", url });
      return {};
    },
    async sendSizeChanged() {
      return {};
    },
    async requestDisplayMode() {
      return {};
    },
    getHostCapabilities: () => ({ message: {} }),
    getHostContext: () => ({ theme: context.theme, locale: context.locale, displayMode: "fullscreen" as const }),
  };

  return {
    app,
    viewId,
    ready: () => readyPromise,
    readWindow: () => ({ path: opened ?? undefined, recent }),
    saveWindow(_patch: unknown) {
      // The Portal owns tabs and recent documents; nothing persists in the frame.
    },
    readDocument: () => opened,
    saveDocument(path: string) {
      opened = path;
    },
    takeResults: () => results.splice(0, results.length),
    handleTool(name: string, handler: PageToolHandler) {
      handlers.set(name, handler);
      for (const wake of waiters) wake();
    },
    get context() {
      return app.getHostContext();
    },
    get capabilities() {
      return app.getHostCapabilities();
    },
    /** Assets and file bytes (office/assets.ts). */
    request,
  };
}

export type Host = ReturnType<typeof createHost>;

declare global {
  interface Window {
    __officeHost?: Host;
  }
}

export function getHost(): Host {
  return (window.__officeHost ??= createHost());
}
