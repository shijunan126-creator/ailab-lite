"use client";

import { useEffect, useState } from "react";

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
 * but nothing in the frame can reach this app's cookies or storage. */
export function HtmlDocFrame({ html, title }: { html: string; title: string }) {
  return <iframe className="viewer-frame" sandbox="allow-scripts allow-popups allow-modals" srcDoc={html} title={title} />;
}
