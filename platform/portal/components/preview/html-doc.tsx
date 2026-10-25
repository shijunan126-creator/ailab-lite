"use client";

import { useEffect, useMemo, useState } from "react";

// Injected into the fetched document: reports the document's real height to the parent, so the
// iframe can be sized to its content and the pane scrolls instead of showing dead space below
// the last slide (scaled-deck layouts keep their unscaled height otherwise).
const HEIGHT_HOOK = `<script>(function(){var post=function(){var b=document.body;var h=b?b.scrollHeight:0;try{parent.postMessage({ailabHtmlHeight:h},"*")}catch(e){}};if(window.ResizeObserver){new ResizeObserver(post).observe(document.body)}window.addEventListener("load",post);var n=0;var t=setInterval(function(){post();if(++n>20)clearInterval(t)},500);post()}())</script>`;

/** Injects the pane integration (height reporting) into a fetched document. */
export function withHeightHook(html: string): string {
  if (/<head[\s>]/i.test(html)) return html.replace(/<head[^>]*>/i, (m) => m + HEIGHT_HOOK);
  return HEIGHT_HOOK + html;
}

/** The reported content height of the rendered frame, while it is on screen. */
export function useHtmlHeight(): number | null {
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const h = (e.data as { ailabHtmlHeight?: unknown } | null)?.ailabHtmlHeight;
      // The frame is origin-less, so this is all the authentication there is - and all that is
      // needed: the message only sets this iframe's own height.
      if (typeof h === "number" && Number.isFinite(h) && h > 0) setHeight(Math.min(Math.ceil(h), 40000));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  return height;
}

/** Fetches a same-origin document URL (the session cookie authorizes it) for iframe rendering. */
export function useDocText(url: string): { html: string | null; error: string } {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let stale = false;
    setHtml(null);
    setError("");
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((h) => !stale && setHtml(h))
      .catch((e) => !stale && setError(String(e?.message || e)));
    return () => {
      stale = true;
    };
  }, [url]);
  return { html, error };
}

/** The rendered document: origin-less and script-capable — the document's own page-turn JS runs,
 * but nothing in the frame can reach this app's cookies or storage. Sized to its real content
 * height; the scroll container is the caller's. */
export function HtmlDocView({ url, title, renderError }: { url: string; title: string; renderError?: (reason: string) => React.ReactNode }) {
  const { html, error } = useDocText(url);
  const height = useHtmlHeight();
  const doc = useMemo(() => (html ? withHeightHook(html) : null), [html]);
  if (error) return renderError ? renderError(error) : <div className="hint" style={{ margin: 16 }}>无法加载：{error}</div>;
  if (doc === null)
    return (
      <div className="viewer-state">
        <span className="viewer-spinner" aria-label="正在加载" />
      </div>
    );
  return <iframe className="viewer-frame" style={height ? { height: `${height}px` } : undefined} sandbox="allow-scripts allow-popups allow-modals" srcDoc={doc} title={title} />;
}
