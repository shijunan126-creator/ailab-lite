"use client";

import { IconFile } from "./icons";

/** Asks the app to show a library file (at a page): one viewer serves every citation on the page. */
export const OPEN_FILE = "ailab:open-file";

/** A library file an answer quotes, as the model named it (《报告》第3页). */
export default function FileCite({ fileId, page, label }: { fileId: string; page?: number; label: string }) {
  const text = label.replace(/^《(.+)》/, "$1") || "文件";
  return (
    <button
      type="button"
      className="md-file"
      title={page ? `打开第 ${page} 页` : "打开文件"}
      onClick={() => window.dispatchEvent(new CustomEvent(OPEN_FILE, { detail: { id: fileId, page } }))}
    >
      <IconFile size={12} />
      <span>{text}</span>
    </button>
  );
}
