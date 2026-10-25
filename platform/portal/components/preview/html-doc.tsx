"use client";

import { useEffect, useMemo, useState } from "react";

// Injected into the fetched document, before any script of its own: the pane resizes this frame
// while it is open, and documents that rebuild their layout on every resize (fixed-canvas decks
// re-wrapping their slides) would otherwise grow a full deck-height per resize — the dead space
// below the last slide. Dropping window "resize" listeners keeps the load-time fit; later
// resizes are ignored, and the document scrolls inside the frame instead.
const RESIZE_GUARD = `<script>(function(){var realAdd=EventTarget.prototype.addEventListener;EventTarget.prototype.addEventListener=function(t,f,o){if(this===window&&t==="resize")return;return realAdd.call(this,t,f,o)}}())</script>`;

/** Injects the resize guard into a fetched document, before any script of its own. */
export function withResizeGuard(html: string): string {
  if (/<head[\s>]/i.test(html)) return html.replace(/<head[^>]*>/i, (m) => m + RESIZE_GUARD);
  return RESIZE_GUARD + html;
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
 * but nothing in the frame can reach this app's cookies or storage. The frame scrolls its own
 * content; the resize guard keeps that content from growing on every pane resize. */
export function HtmlDocView({ url, title, renderError }: { url: string; title: string; renderError?: (reason: string) => React.ReactNode }) {
  const { html, error } = useDocText(url);
  const doc = useMemo(() => (html ? withResizeGuard(html) : null), [html]);
  if (error) return renderError ? renderError(error) : <div className="hint" style={{ margin: 16 }}>无法加载：{error}</div>;
  if (doc === null)
    return (
      <div className="viewer-state">
        <span className="viewer-spinner" aria-label="正在加载" />
      </div>
    );
  return <iframe className="viewer-frame" sandbox="allow-scripts allow-popups allow-modals" srcDoc={doc} title={title} />;
}
