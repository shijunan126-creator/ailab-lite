import { NextRequest, NextResponse } from "next/server";

// (Next 16 renamed the `middleware` file convention to `proxy`.)
//
// Every page gets a Content-Security-Policy with a fresh nonce: Next reads it from the request header and puts it on
// its own scripts (the root layout puts it on its inline ones), so a script injected into a page cannot run.
// `strict-dynamic` lets those scripts load the app's chunks. Styles allow inline because React writes style
// attributes. Frames may only show this origin: app/frame/[kind] (which sends its own policy) and the API's
// reader render; a sandboxed frame that tries to navigate elsewhere is blocked by frame-src. No page of the Portal
// is ever framed, by itself or anyone else.
//
// Pages also need a session cookie; without one the visitor gets the login page *at the same URL* (a rewrite, not a
// redirect: Next rewrites the host inside redirect URLs, which sends visitors arriving via 127.0.0.1, a LAN name or a
// proxy to another origin where this cookie does not exist). The login page sends them back here once they are in.
// Whether the token is still valid is the API's call: the client returns here on a 401.

const dev = process.env.NODE_ENV === "development";

function policy(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${dev ? " ws: wss:" : ""}`,
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "frame-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

const PUBLIC = /^\/(login|register)(\/|$)/;

export function proxy(req: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const csp = policy(nonce);
  const headers = new Headers(req.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const signedIn = PUBLIC.test(req.nextUrl.pathname) || !!req.cookies.get("ailab_token")?.value;
  const res = signedIn
    ? NextResponse.next({ request: { headers } })
    : NextResponse.rewrite(new URL("/login", req.url), { request: { headers } });
  res.headers.set("Content-Security-Policy", csp);
  return res;
}

export const config = { matcher: ["/((?!api|_next/static|_next/image|frame/|favicon.ico).*)"] };
