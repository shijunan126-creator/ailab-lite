import { createHash } from "node:crypto";
import { FRAME_KINDS, SHELL, framePolicy, type FrameKind } from "../../../lib/frame";

const HASH = `sha256-${createHash("sha256").update(SHELL, "utf8").digest("base64")}`;

export const dynamic = "force-static";
export function generateStaticParams() {
  return FRAME_KINDS.map((kind) => ({ kind }));
}

export async function GET(_req: Request, { params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  if (!(FRAME_KINDS as readonly string[]).includes(kind)) return new Response("not found", { status: 404 });
  const html = `<!doctype html><html data-kind="${kind}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><script>${SHELL}</script></body></html>`;
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": framePolicy(kind as FrameKind, HASH),
      "Cache-Control": "public, max-age=3600",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  });
}
