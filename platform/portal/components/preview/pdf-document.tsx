"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

// Module-level constant: react-pdf tears down and re-fetches the whole document whenever the options
// identity changes, and an inline literal would reload the PDF on every chat stream event.
// isEvalSupported: the Portal CSP forbids eval, and PDF.js would otherwise probe it (a logged violation per file).
const PDF_OPTIONS = { withCredentials: true, isEvalSupported: false };

/** PDF.js preview: selectable text, zoom, lazy-ish page rendering; the backend only has to produce the PDF. */
export default function PdfDocument({ url, title }: { url: string; title: string }) {
  const [pages, setPages] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(720);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const measure = () => setWidth(Math.max(280, element.clientWidth - 32));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const shown = Math.min(pages, 40);
  return (
    <div className="pdf-preview">
      <div className="pdf-toolbar">
        <span className="hint">{pages ? `${pages} 页` : "加载中…"}</span>
        <span className="spacer" />
        <button type="button" className="link" onClick={() => setZoom((value) => Math.max(0.6, Math.round((value - 0.1) * 10) / 10))} aria-label="缩小">
          −
        </button>
        <span className="pdf-zoom">{Math.round(zoom * 100)}%</span>
        <button type="button" className="link" onClick={() => setZoom((value) => Math.min(2, Math.round((value + 0.1) * 10) / 10))} aria-label="放大">
          +
        </button>
      </div>
      <div className="pdf-scroll" ref={box} aria-label={title}>
        {error ? <div className="alert error">{error}</div> : null}
        <Document
          file={url}
          options={PDF_OPTIONS}
          onLoadSuccess={({ numPages }) => {
            setPages(numPages);
            setError("");
          }}
          onLoadError={(reason) => setError(`PDF 加载失败：${reason?.message || reason}`)}
          loading={<div className="viewer-state"><span className="viewer-spinner" aria-label="正在加载 PDF" /></div>}
        >
          {Array.from({ length: shown }, (_, index) => (
            <Page
              key={index + 1}
              pageNumber={index + 1}
              width={width * zoom}
              renderTextLayer
              renderAnnotationLayer={false}
              loading=""
              className="pdf-page"
              devicePixelRatio={typeof window === "undefined" ? 1 : Math.min(2, window.devicePixelRatio || 1)}
            />
          ))}
        </Document>
        {pages > shown ? <div className="hint" style={{ padding: 12 }}>只显示前 {shown} 页，共 {pages} 页。</div> : null}
      </div>
    </div>
  );
}
