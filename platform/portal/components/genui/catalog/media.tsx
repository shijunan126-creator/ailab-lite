"use client";

// Pictures in an answer: one image, a gallery, an image beside its text. Every picture is a platform-signed web image
// a tool result handed the model (data.ts webImage); anything else is not loaded. A picture that fails to load drops
// out of a gallery and shows as a quiet placeholder elsewhere. Clicking one opens the pictures at full size.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { API_BASE } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { IconChevronRight, IconExternal, IconImage, IconX } from "../../icons";
import { MAX_IMAGES, objects, siteOf, strings, text, webImage } from "./data";

type P = Record<string, any>;
export type Picture = { src: string | null; alt: string; caption: string; source: string; site: string | null };

export function picture(v: { src?: unknown; alt?: unknown; caption?: unknown; source?: unknown }): Picture {
  const site = siteOf(v.source);
  return { src: webImage(v.src), alt: text(v.alt) || text(v.caption), caption: text(v.caption), source: site ? text(v.source) : "", site };
}

const url = (p: Picture) => `${API_BASE}${p.src}`;

function SourceChip({ p }: { p: Picture }) {
  if (!p.site) return null;
  return (
    <a className="gu-photo-source" href={p.source} target="_blank" rel="noopener noreferrer" title={p.source}>
      {p.site}
    </a>
  );
}

/** One picture in its frame: loading shimmer, the image, the source on it; `onOpen` makes it zoomable. */
function Photo({ p, onOpen, onFail, overlay, className }: { p: Picture; onOpen?: () => void; onFail?: () => void; overlay?: ReactNode; className?: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">(p.src ? "loading" : "failed");
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {
    // already in the cache: the load event fired before React attached the handler
    setState(!p.src ? "failed" : img.current?.complete && img.current.naturalWidth ? "ready" : "loading");
  }, [p.src]);
  useEffect(() => {
    if (state === "failed") onFail?.();
  }, [state, onFail]);
  return (
    <div className={cn("gu-photo", className)} data-state={state}>
      {state === "failed" ? (
        <div className="gu-photo-missing" role="img" aria-label={p.alt || "图片无法显示"}>
          <IconImage size={22} />
        </div>
      ) : (
        <button type="button" className="gu-photo-open" onClick={onOpen} disabled={!onOpen} aria-label={p.alt ? `查看大图：${p.alt}` : "查看大图"}>
          <img ref={img} src={url(p)} alt={p.alt} loading="lazy" decoding="async" draggable={false} onLoad={() => setState("ready")} onError={() => setState("failed")} />
          {overlay}
        </button>
      )}
      {state !== "failed" ? <SourceChip p={p} /> : null}
    </div>
  );
}

/** The pictures at full size, one at a time: arrows and the keyboard step through, Escape or the backdrop close. */
function Lightbox({ items, at, onClose }: { items: Picture[]; at: number; onClose: () => void }) {
  const [i, setI] = useState(at);
  const close = useRef<HTMLButtonElement>(null);
  const step = useCallback((d: number) => setI((n) => (n + d + items.length) % items.length), [items.length]);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    document.addEventListener("keydown", onKey, true);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, [onClose, step]);
  const p = items[Math.min(i, items.length - 1)];
  const many = items.length > 1;
  return createPortal(
    <div className="gu-lightbox" role="dialog" aria-modal="true" aria-label={p.alt || "图片"} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <button ref={close} type="button" className="gu-lightbox-close" onClick={onClose} aria-label="关闭">
        <IconX size={20} />
      </button>
      {many ? (
        <button type="button" className="gu-lightbox-nav prev" onClick={() => step(-1)} aria-label="上一张">
          <IconChevronRight size={22} />
        </button>
      ) : null}
      <figure className="gu-lightbox-figure" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <img key={p.src} src={url(p)} alt={p.alt} />
        {p.caption || p.site || many ? (
          <figcaption>
            {many ? <span className="gu-lightbox-count">{i + 1} / {items.length}</span> : null}
            {p.caption ? <span>{p.caption}</span> : null}
            {p.site ? (
              <a href={p.source} target="_blank" rel="noopener noreferrer">
                {p.site} <IconExternal size={13} />
              </a>
            ) : null}
          </figcaption>
        ) : null}
      </figure>
      {many ? (
        <button type="button" className="gu-lightbox-nav next" onClick={() => step(1)} aria-label="下一张">
          <IconChevronRight size={22} />
        </button>
      ) : null}
    </div>,
    document.body,
  );
}

/** Which picture is open full size, over which list. */
function useViewer(items: Picture[]) {
  const [at, setAt] = useState<number | null>(null);
  const close = useCallback(() => setAt(null), []);
  const viewer = at === null || !items.length ? null : <Lightbox items={items} at={Math.min(at, items.length - 1)} onClose={close} />;
  return { open: (i: number) => setAt(i), viewer };
}

const RATIOS: Record<string, string> = { "16:9": "16 / 9", "4:3": "4 / 3", "1:1": "1 / 1", "3:4": "3 / 4" };

export function ImageView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const pic = picture({ src: p.src, alt: o.alt, caption: o.caption, source: o.source });
  const { open, viewer } = useViewer([pic]);
  const ratio = RATIOS[text(o.ratio)];
  return (
    <figure className="gu-figure" style={ratio ? ({ "--ratio": ratio } as React.CSSProperties) : undefined} data-ratio={ratio ? "fixed" : "auto"}>
      <Photo p={pic} onOpen={() => open(0)} />
      {pic.caption ? <figcaption className="gu-figure-caption">{pic.caption}</figcaption> : null}
      {viewer}
    </figure>
  );
}

export function GalleryView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const all = (objects<P>(p.images, ["src"], MAX_IMAGES) ?? []).map(picture);
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set());
  const items = all.filter((it) => it.src && !failed.has(it.src));
  const { open, viewer } = useViewer(items);
  const fails = useRef(new Map<string, () => void>());
  // one stable callback per picture: Photo reports a failure from an effect that depends on it
  const fail = (src: string) => {
    let f = fails.current.get(src);
    if (!f) fails.current.set(src, (f = () => setFailed((s) => (s.has(src) ? s : new Set(s).add(src)))));
    return f;
  };
  if (!items.length) {
    return (
      <figure className="gu-figure" data-ratio="auto">
        <Photo p={picture({})} />
      </figure>
    );
  }
  const shown = items.slice(0, 3);
  return (
    <figure className="gu-gallery" data-count={shown.length}>
      {o.title ? <figcaption className="gu-card-title">{text(o.title)}</figcaption> : null}
      <div className="gu-gallery-row">
        {shown.map((it, i) => (
          <Photo
            key={it.src}
            p={it}
            onOpen={() => open(i)}
            onFail={fail(it.src!)}
            overlay={
              <>
                {/* a narrow column shows two tiles: the second then counts the rest */}
                {i === 1 && items.length > 2 ? <span className="gu-gallery-more narrow">+{items.length - 2}</span> : null}
                {i === 2 && items.length > 3 ? <span className="gu-gallery-more wide">+{items.length - 3}</span> : null}
              </>
            }
          />
        ))}
      </div>
      {shown.length === 1 && shown[0].caption ? <figcaption className="gu-figure-caption">{shown[0].caption}</figcaption> : null}
      {viewer}
    </figure>
  );
}

export function ImageTextView({ p }: { p: P }) {
  const o: P = p.opts || {};
  const pic = picture({ src: p.src, alt: p.title, source: o.source });
  const points = strings(o.points, 20) ?? [];
  const size = ["sm", "md", "lg"].includes(o.size) ? o.size : "md";
  const { open, viewer } = useViewer([pic]);
  return (
    <section className="gu-imagetext" data-size={size} data-side={o.side === "right" ? "right" : "left"}>
      <div className="gu-imagetext-inner">
      <Photo p={pic} onOpen={() => open(0)} />
      <div className="gu-imagetext-body">
        <h4 className="gu-imagetext-title">
          {pic.site ? (
            <a href={pic.source} target="_blank" rel="noopener noreferrer">{text(p.title)}</a>
          ) : (
            text(p.title)
          )}
        </h4>
        {o.text ? <p className="gu-text">{text(o.text)}</p> : null}
        {points.length ? <ul className="gu-imagetext-points">{points.map((t, i) => <li key={i}>{t}</li>)}</ul> : null}
      </div>
      </div>
      {viewer}
    </section>
  );
}

/** The cover picture of an EntityCard (opts.image); nothing when it is not a picture this answer may show. */
export function CardImage({ src, alt }: { src: unknown; alt: string }) {
  const pic = picture({ src, alt });
  const [failed, setFailed] = useState(false);
  const hide = useCallback(() => setFailed(true), []);
  const { open, viewer } = useViewer([pic]);
  // a card reads fine without its picture: a broken one is left out rather than shown as an empty frame
  if (!pic.src || failed) return null;
  return (
    <>
      <Photo p={pic} onOpen={() => open(0)} onFail={hide} className="gu-entity-image" />
      {viewer}
    </>
  );
}
