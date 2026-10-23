import { NextRequest, NextResponse } from "next/server";
import { forwardedFor, isHttps, sameOrigin, UPSTREAM } from "../../../../../lib/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
  try {
    const upstream = await fetch(`${UPSTREAM}/v1/auth/oidc/start`, {
      method: "POST", headers: forwardedFor(req), cache: "no-store", signal: AbortSignal.timeout(20000),
    });
    if (!upstream.ok) return NextResponse.json({ detail: "统一身份登录暂不可用" }, { status: upstream.status });
    const { authorization_url, flow_token, expires_in } = await upstream.json();
    const res = NextResponse.redirect(authorization_url, 303);
    // Lax allows the top-level redirect back from the IdP. The final platform session stays Strict.
    res.cookies.set("ailab_oidc_flow", flow_token, {
      httpOnly: true, sameSite: "lax", secure: isHttps(req), path: "/api/auth/oidc", maxAge: Math.min(expires_in, 900),
    });
    res.headers.set("cache-control", "no-store");
    res.headers.set("referrer-policy", "no-referrer");
    return res;
  } catch {
    return NextResponse.json({ detail: "统一身份登录暂不可用" }, { status: 502 });
  }
}
