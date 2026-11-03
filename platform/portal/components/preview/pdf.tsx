"use client";

import dynamic from "next/dynamic";
import DocLoading from "./doc-loading";

const PdfDocument = dynamic(() => import("./pdf-document"), {
  ssr: false,
  loading: () => <DocLoading shape="page" />,
});

export default PdfDocument;
