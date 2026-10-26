"use client";

import dynamic from "next/dynamic";

const PdfDocument = dynamic(() => import("./pdf-document"), {
  ssr: false,
  loading: () => (
    <div className="viewer-state">
      <span className="viewer-spinner" aria-label="正在加载 PDF" />
    </div>
  ),
});

export default PdfDocument;
