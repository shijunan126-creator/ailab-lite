"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api, errorMessage, redirectIfUnauthorized, type FileItem } from "../../lib/api";
import { writeClipboard } from "../../lib/clipboard";
import { IconCheck, IconCopy, IconDownload, IconRetry } from "../icons";
import Modal from "./modal";

type Progress = { status: string; reason?: string; download_url?: string | null };
type View = { phase: "busy" } | { phase: "done"; text: string; href: string } | { phase: "failed"; message: string };

/** Pages one request may read (the API refuses more). */
const BATCH = 20;
const POLL_MS = 1500;
const WAIT_MS = 120_000;

/** "1-5,8" → [1..5, 8]; throws a message the reader can act on. */
export function parsePages(input: string, count: number | null): number[] {
  const pages: number[] = [];
  for (const token of input.split(/[,，\s]+/).filter(Boolean)) {
    const match = /^(\d+)(?:\s*[-–~]\s*(\d+))?$/.exec(token);
    if (!match) throw new Error("页码格式如 1-20 或 3,5");
    const first = Number(match[1]);
    const last = Number(match[2] || match[1]);
    if (first < 1 || last < first || (count && last > count)) throw new Error(count ? `页码在 1–${count} 之间` : "页码从 1 开始");
    for (let page = first; page <= last && pages.length <= BATCH; page++) if (!pages.includes(page)) pages.push(page);
  }
  if (!pages.length) throw new Error("请输入页码");
  if (pages.length > BATCH) throw new Error(`每次最多 ${BATCH} 页`);
  return pages;
}

const range = (count: number | null) => `1-${Math.min(BATCH, count || BATCH)}`;
const pageCount = (file: FileItem | null): number | null => {
  const extract = file?.meta?.extract;
  const n = Number(extract?.units ?? extract?.pages);
  return Number.isInteger(n) && n > 0 ? n : null;
};

/** Text from a scanned PDF or an image: reading starts as the dialog opens, and the text shows here to copy or
 * save. A PDF longer than one batch reads its first pages; another range is one edit away. */
export default function TextExtraction({ fileId, filename, onClose }: { fileId: string; filename: string; onClose: () => void }) {
  const pdf = /\.pdf$/i.test(filename);
  const [count, setCount] = useState<number | null>(null);
  const [pages, setPages] = useState("");
  const [view, setView] = useState<View>({ phase: "busy" });
  const [copied, setCopied] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const run = useCallback(
    async (selected: number[] | null) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      const signal = current.signal;
      setView({ phase: "busy" });
      setCopied(false);
      try {
        const requested = await api<{ request_id: string }>(`/v1/files/${fileId}/extract-text`, {
          method: "POST",
          signal,
          body: JSON.stringify(selected ? { pages: selected } : {}),
        });
        const deadline = Date.now() + WAIT_MS;
        let progress: Progress;
        for (;;) {
          progress = await api<Progress>(`/v1/files/${fileId}/extract-text?request_id=${encodeURIComponent(requested.request_id)}`, { signal });
          if (!["pending", "running"].includes(progress.status)) break;
          if (Date.now() > deadline) throw new Error("仍在识别，请稍后重试");
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, POLL_MS);
            signal.addEventListener("abort", () => (clearTimeout(timer), resolve()), { once: true });
          });
          if (signal.aborted) return;
        }
        if (progress.status !== "done" || !progress.download_url) throw new Error(progress.reason || "没能识别文字，请重试");
        const href = `${API_BASE}${progress.download_url}`;
        const resp = await fetch(href, { signal, cache: "no-store" });
        redirectIfUnauthorized(resp);
        if (!resp.ok) throw new Error(`读取失败（${resp.status}）`);
        const text = await resp.text();
        if (controller.current === current) setView({ phase: "done", text, href });
      } catch (err) {
        if (!signal.aborted && controller.current === current) setView({ phase: "failed", message: errorMessage(err) });
      }
    },
    [fileId]
  );

  // Open → read. A PDF first learns its length, so a short one is read whole and a long one from its first batch.
  useEffect(() => {
    const ctl = new AbortController();
    if (!pdf) run(null);
    else
      api<FileItem>(`/v1/files/${fileId}`, { signal: ctl.signal })
        .catch(() => null)
        .then((file) => {
          if (ctl.signal.aborted) return;
          const n = pageCount(file);
          setCount(n);
          setPages(range(n));
          run(parsePages(range(n), n));
        });
    return () => {
      ctl.abort();
      controller.current?.abort();
    };
  }, [fileId, pdf, run]);

  function reread() {
    if (!pdf) return run(null);
    try {
      run(parsePages(pages, count));
    } catch (err) {
      setView({ phase: "failed", message: errorMessage(err) });
    }
  }

  async function copy(text: string) {
    try {
      await writeClipboard({ plain: text });
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // nothing to undo: the button simply does not turn into a check
    }
  }

  const busy = view.phase === "busy";
  // a PDF that fits one batch is read whole; only a longer one (or one of unknown length) offers a page range
  const ranged = pdf && (count === null || count > BATCH);
  const footer =
    view.phase === "done" ? (
      <>
        <a className="btn secondary sm" href={view.href} download>
          <IconDownload size={15} /> 下载
        </a>
        <span className="spacer" />
        <button className="sm" onClick={() => copy(view.text)} disabled={!view.text.trim()}>
          {copied ? <IconCheck size={15} /> : <IconCopy size={15} />} {copied ? "已复制" : "复制全部"}
        </button>
      </>
    ) : view.phase === "failed" ? (
      <>
        <span className="spacer" />
        <button className="sm" onClick={reread}>
          <IconRetry size={15} /> 重试
        </button>
      </>
    ) : undefined;

  return (
    <Modal
      title="提取文字"
      onClose={onClose}
      className={`text-extract ${view.phase === "failed" || (view.phase === "done" && !view.text.trim()) ? "compact" : ""}`}
      footer={footer}
    >
      {ranged && pages ? (
        <form
          className="text-extract-range"
          onSubmit={(e) => {
            e.preventDefault();
            reread();
          }}
        >
          <input type="text" inputMode="numeric" aria-label="页码" value={pages} onChange={(e) => setPages(e.target.value)} disabled={busy} spellCheck={false} />
          {count ? <span className="hint">共 {count} 页</span> : null}
          <button type="submit" className="secondary sm" disabled={busy}>
            识别
          </button>
        </form>
      ) : null}
      {view.phase === "busy" ? (
        <div className="text-extract-wait" role="status">
          <span className="draft-spin" /> 正在识别…
        </div>
      ) : view.phase === "failed" ? (
        <div role="alert" className="alert error">{view.message}</div>
      ) : view.text.trim() ? (
        <pre className="text-extract-text" tabIndex={0} aria-label={`${filename} 的文字`}>{view.text}</pre>
      ) : (
        <div className="text-extract-wait">没有识别到文字</div>
      )}
    </Modal>
  );
}
