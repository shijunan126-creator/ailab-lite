"use client";

import { useEffect, useRef, useState } from "react";

/** Native scrolling is shared by the work pane and the full-screen viewer. */
export default function DocumentPages({ pages, src, documentKey, total = pages.length, thumbnails = false, initialPage, children }: {
  pages: string[];
  initialPage?: number; // 1-based: shown first (a citation of that page)
  src: (name: string) => string;
  documentKey: string;
  total?: number;
  thumbnails?: boolean;
  children?: React.ReactNode;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [current, setCurrent] = useState(0);
  const [ratios, setRatios] = useState<Record<string, string>>({});
  const pageKey = pages.join("|");
  useEffect(() => {
    setCurrent(0);
    setRatios({});
    if (box.current) box.current.scrollTop = 0;
  }, [documentKey]);
  // a cited page: scroll there once its place is known (pages above it reserve their height through aspect-ratio)
  useEffect(() => {
    if (!initialPage || initialPage < 2 || initialPage > pages.length) return;
    const frame = requestAnimationFrame(() => {
      const root = box.current;
      const page = root?.querySelector<HTMLElement>(`[data-page="${initialPage}"]`);
      if (root && page) root.scrollTop += page.getBoundingClientRect().top - root.getBoundingClientRect().top;
    });
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentKey, initialPage, pages.length, ratios[pages[0]]]); // again once the first page's shape sets the others' height
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    const observer = new IntersectionObserver((entries) => {
      const middle = root.getBoundingClientRect().top + root.clientHeight * 0.15;
      const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => {
        const distance = (e: IntersectionObserverEntry) => Math.abs(e.boundingClientRect.top + e.boundingClientRect.height / 2 - middle);
        return distance(a) - distance(b);
      });
      if (visible.length) setCurrent(Number((visible[0].target as HTMLElement).dataset.page) - 1);
    }, { root, rootMargin: "-10% 0px -80% 0px" });
    root.querySelectorAll("[data-page]").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [pageKey, documentKey]);
  const index = Math.min(current, Math.max(0, pages.length - 1));
  function jump(i: number) {
    const root = box.current;
    const page = root?.querySelector<HTMLElement>(`[data-page="${i + 1}"]`);
    if (!root || !page) return;
    const top = root.scrollTop + page.getBoundingClientRect().top - root.getBoundingClientRect().top;
    root.scrollTo({ top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
  return (
    <div className="document-pages" onKeyDown={(event) => {
      if (!thumbnails || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      jump(Math.max(0, Math.min(pages.length - 1, index + (event.key === "ArrowRight" ? 1 : -1))));
    }}>
      <div className="document-scroll" ref={box} tabIndex={0} aria-label="文档页面">
        {pages.map((name, i) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={name} data-page={i + 1} src={src(name)} alt={`第 ${i + 1} 页`} loading={i < 2 ? "eager" : "lazy"}
            decoding="async" style={{ aspectRatio: ratios[name] || ratios[pages[0]] || "1 / 1.414" }} onLoad={(event) => {
              const ratio = `${event.currentTarget.naturalWidth} / ${event.currentTarget.naturalHeight}`;
              setRatios((previous) => previous[name] === ratio ? previous : { ...previous, [name]: ratio });
            }} />
        ))}
        {children}
      </div>
      {pages.length > 1 ? <span className="document-counter" aria-live="off">{index + 1} / {total}</span> : null}
      {thumbnails ? <div className="thumbs" aria-label="页面导航">
        {pages.map((name, i) => (
          <button key={name} className={`thumb ${i === index ? "on" : ""}`} onClick={() => jump(i)} aria-label={`第 ${i + 1} 页`} aria-current={i === index ? "page" : undefined}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src(name)} alt="" loading="lazy" /><span>{i + 1}</span>
          </button>
        ))}
      </div> : null}
    </div>
  );
}
