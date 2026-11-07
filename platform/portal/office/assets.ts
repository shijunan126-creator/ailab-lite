// AILab replacement for the runtime's ui/assets.ts. The Portal fetches viewer assets from its own origin and hands
// them to the frame; document bytes arrive as one transferred ArrayBuffer instead of base64 chunks, so the frame holds
// a single copy of the file.
import type { FileHandle } from "#office/contract.ts";
import { getHost } from "#ui/host.ts";

const textCache = new Map<string, Promise<string>>();
const binaryCache = new Map<string, Promise<Uint8Array<ArrayBuffer>>>();

export function readText(name: string): Promise<string> {
  let cached = textCache.get(name);
  if (!cached) {
    cached = getHost().request<string>({ t: "asset", name, binary: false });
    cached.catch(() => textCache.delete(name));
    textCache.set(name, cached);
  }
  return cached;
}

/** Binary asset (wasm, pdf.js cMaps and fonts); cached for the life of the page. */
export function readBinary(name: string): Promise<Uint8Array<ArrayBuffer>> {
  let cached = binaryCache.get(name);
  if (!cached) {
    cached = getHost()
      .request<ArrayBuffer>({ t: "asset", name, binary: true })
      .then((buffer) => new Uint8Array(buffer));
    cached.catch(() => binaryCache.delete(name));
    binaryCache.set(name, cached);
  }
  return cached;
}

/** A loaded document's bytes; the token is released by the Portal once handed over. */
export async function readFileBytes(handle: FileHandle, signal?: AbortSignal, kind?: string): Promise<ArrayBuffer> {
  signal?.throwIfAborted();
  const buffer = await getHost().request<ArrayBuffer>({ t: "bytes", token: handle.token });
  if (buffer.byteLength !== handle.bytes) throw new Error("文件在加载时发生了变化，请重新打开。");
  signal?.throwIfAborted();
  if (kind && kind !== "pdf") {
    const {admit} = await import("./admission");
    return await admit(buffer, kind, signal);
  }
  return buffer;
}
