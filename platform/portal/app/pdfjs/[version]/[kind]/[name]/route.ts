import { readFile } from "node:fs/promises";
import path from "node:path";

// PDF.js data files (CJK CMaps, standard fonts, JPX/JBIG2 decoders, ICC profiles) served from the installed
// pdfjs-dist. Without them a font that relies on a predefined CMap (e.g. STSong + UniGB-UCS2-H, which reportlab's
// Chinese PDFs use) renders as an empty line. The path carries the version, so a response never changes.
const ROOT = path.join(process.cwd(), "node_modules", "pdfjs-dist");
const KINDS = new Set(["cmaps", "standard_fonts", "wasm", "iccs"]);
const TYPES: Record<string, string> = { ".wasm": "application/wasm", ".js": "text/javascript; charset=utf-8" };

let installed: Promise<string> | undefined;
const version = () => (installed ??= readFile(path.join(ROOT, "package.json"), "utf8").then((text) => JSON.parse(text).version));

export async function GET(_req: Request, { params }: { params: Promise<{ version: string; kind: string; name: string }> }) {
  const { version: wanted, kind, name } = await params;
  if (!KINDS.has(kind) || !/^[\w-][\w.-]*$/.test(name) || wanted !== (await version())) return new Response("not found", { status: 404 });
  let body: Buffer;
  try {
    body = await readFile(path.join(ROOT, kind, name));
  } catch {
    return new Response("not found", { status: 404 });
  }
  return new Response(new Uint8Array(body), {
    headers: {
      "Content-Type": TYPES[path.extname(name)] ?? "application/octet-stream",
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
