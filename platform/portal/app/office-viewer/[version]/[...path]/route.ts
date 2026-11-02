import { readFile } from "node:fs/promises";
import path from "node:path";
import { assetEncoding } from "../../../../office/encoding";

// The side-pane document viewer (office/build.mjs output). The Portal page fetches these and hands them to the
// sandboxed frame; the path carries the build version, so a response never changes. `current` answers the version.
const ROOT = path.join(process.cwd(), "office", "dist");
const TYPES: Record<string, string> = {
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

let manifest: Promise<{ version: string }> | undefined;
const built = () => (manifest ??= readFile(path.join(ROOT, "manifest.json"), "utf8").then((text) => JSON.parse(text)));

export async function GET(req: Request, { params }: { params: Promise<{ version: string; path: string[] }> }) {
  const { version, path: parts } = await params;
  let current: string;
  try {
    current = (await built()).version;
  } catch {
    manifest = undefined;
    return new Response("not built", { status: 404 });
  }
  if (version === "current" && parts.join("/") === "manifest.json")
    return Response.json({ version: current }, { headers: { "Cache-Control": "no-cache" } });
  if (version !== current || !parts.length || parts.some((part) => !/^[\w-][\w.-]*$/.test(part))) return new Response("not found", { status: 404 });
  let body: Buffer;
  let encoding = assetEncoding(req.headers.get("Accept-Encoding"));
  try {
    const filename = path.join(ROOT, ...parts);
    try {
      if (!encoding) throw new Error("identity");
      body = await readFile(filename + (encoding === "br" ? ".br" : ".gz"));
    } catch {
      encoding = null;
      body = await readFile(filename);
    }
  } catch {
    return new Response("not found", { status: 404 });
  }
  return new Response(new Uint8Array(body), {
    headers: {
      "Content-Type": TYPES[path.extname(parts[parts.length - 1])] ?? "application/octet-stream",
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-Content-Type-Options": "nosniff",
      "Vary": "Accept-Encoding",
      "Content-Length": String(body.byteLength),
      ...(encoding ? { "Content-Encoding": encoding } : {}),
    },
  });
}
