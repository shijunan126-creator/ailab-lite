import { NextRequest, NextResponse } from "next/server";
import { COOKIE, isHttps, sessionResponse, UPSTREAM } from "../../../../../lib/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  let res: NextResponse;
  const flowToken = req.cookies.get("ailab_oidc_flow")?.value;
  try {
    if (!flowToken) throw new Error("missing flow");
    const upstream = await fetch(`${UPSTREAM}/v1/auth/oidc/callback`, {
      method: "POST", headers: { "content-type": "application/json" }, cache: "no-store", signal: AbortSignal.timeout(20000),
      body: JSON.stringify({ state: req.nextUrl.searchParams.get("state") || "", code: req.nextUrl.searchParams.get("code") || "",
        error: req.nextUrl.searchParams.get("error"), flow_token: flowToken }),
    });
    if (!upstream.ok) throw new Error("authentication refused");
    const { token, expires_in } = await upstream.json();
    // A relative Location preserves the visitor's host behind Next's normalized request URL.
    res = new NextResponse(null, { status: 303, headers: { location: "/" } });
    const cookie = sessionResponse(req, token, expires_in).cookies.get(COOKIE)!;
    res.cookies.set(cookie);
  } catch {
    res = new NextResponse(null, { status: 303, headers: { location: "/login?error=oidc" } });
  }
  res.cookies.set("ailab_oidc_flow", "", { httpOnly: true, sameSite: "lax", secure: isHttps(req), path: "/api/auth/oidc", maxAge: 0 });
  res.headers.set("cache-control", "no-store");
  res.headers.set("referrer-policy", "no-referrer");
  return res;
}
