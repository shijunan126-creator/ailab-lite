"use client";

import { useEffect, useRef } from "react";
import type { FrameKind, FrameMessage } from "../lib/frame";

type Props = {
  kind: FrameKind;
  /** The document to show. A preview frame repaints each newer value; the others take the first one. */
  html: string;
  title: string;
  className?: string;
  sandbox?: string;
  style?: React.CSSProperties;
  onHeight?: (h: number) => void;
  /** The frame navigated away from its document (a link or script left it): the host should take it down. */
  onLeave?: () => void;
  /** Theme stylesheet sent to a running generated page when the theme changes. */
  theme?: string;
};

/** An iframe on app/frame/[kind]: its own CSP, an opaque origin, the document delivered by postMessage. */
export function SandboxFrame({ kind, html, title, className, sandbox = "allow-scripts", style, onHeight, onLeave, theme }: Props) {
  const ref = useRef<HTMLIFrameElement>(null);
  const ready = useRef(false);
  const latest = useRef({ html, onHeight, onLeave });
  latest.current = { html, onHeight, onLeave };

  useEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    ready.current = false;
    let loads = 0;
    const onMessage = (e: MessageEvent) => {
      // origin is "null" for every sandboxed frame; the window identifies ours
      if (e.source !== frame.contentWindow || !e.data || typeof e.data !== "object") return;
      const msg = e.data as FrameMessage;
      if (msg.ailab === "ready") {
        ready.current = true;
        frame.contentWindow?.postMessage({ html: latest.current.html }, "*");
      } else if (msg.ailab === "height" && Number.isFinite(msg.h)) latest.current.onHeight?.(msg.h);
    };
    // The shell loads once; a frame that writes its document (document.open/close) loads a second time. Any load
    // after that is the frame going somewhere else.
    const expected = kind === "genui-preview" ? 1 : 2;
    const onLoad = () => {
      if (++loads > expected) latest.current.onLeave?.();
    };
    window.addEventListener("message", onMessage);
    frame.addEventListener("load", onLoad);
    return () => {
      window.removeEventListener("message", onMessage);
      frame.removeEventListener("load", onLoad);
    };
  }, [kind]);

  useEffect(() => {
    if (kind === "genui-preview" && ready.current) ref.current?.contentWindow?.postMessage({ html }, "*");
  }, [kind, html]);

  useEffect(() => {
    if (kind === "genui-run" && theme && ready.current) ref.current?.contentWindow?.postMessage({ theme }, "*");
  }, [kind, theme]);

  return <iframe ref={ref} key={kind} src={`/frame/${kind}`} sandbox={sandbox} referrerPolicy="no-referrer" title={title} className={className} style={style} />;
}
