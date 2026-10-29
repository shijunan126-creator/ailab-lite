"use client";

import {useEffect, useRef, useState} from "react";
import {api, errorMessage} from "../../lib/api";
import Modal from "./modal";

type Progress = {status: string; reason?: string; download_url?: string};

export default function TextExtraction({fileId, filename, onClose}: {fileId: string; filename: string; onClose: () => void}) {
  const [range, setRange] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), [fileId]);
  const pdf = /\.pdf$/i.test(filename);
  async function start() {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setError(""); setBusy(true); setResult(null);
    try {
      const pages: number[] = [];
      if (pdf) {
        for (const token of range.split(/[,，\s]+/).filter(Boolean)) {
          const match = /^(\d+)(?:-(\d+))?$/.exec(token);
          if (!match) throw new Error("请输入页码，例如 1-5,85");
          const first = Number(match[1]), last = Number(match[2] || match[1]);
          if (first < 1 || last < first || last > 100000 || last - first > 19) throw new Error("每批请选择1至20页");
          for (let page = first; page <= last; page++) if (!pages.includes(page)) pages.push(page);
        }
        if (!pages.length || pages.length > 20) throw new Error("每批请选择1至20页");
      }
      await api(`/v1/files/${fileId}/extract-text`, {method: "POST", signal: current.signal, body: JSON.stringify(pdf ? {pages} : {})});
      const deadline = Date.now() + 120000;
      while (!current.signal.aborted && Date.now() < deadline) {
        const progress = await api<Progress>(`/v1/files/${fileId}/extract-text`, {signal: current.signal});
        if (!['pending', 'running'].includes(progress.status)) {
          if (controller.current === current) setResult(progress);
          return;
        }
        await new Promise<void>((resolve) => {
          const done = () => { clearTimeout(timer); current.signal.removeEventListener("abort", done); resolve(); };
          const timer = setTimeout(done, 1500);
          current.signal.addEventListener("abort", done, {once: true});
        });
      }
      if (!current.signal.aborted) throw new Error("仍在处理，可稍后重新查看");
    } catch (err) {
      if (!current.signal.aborted && controller.current === current) setError(errorMessage(err));
    } finally {
      if (controller.current === current) setBusy(false);
    }
  }
  return <Modal title="提取文字" onClose={onClose} footer={result?.status === 'done' && result.download_url
    ? <a className="btn primary" href={`/api${result.download_url}`} download>下载文字</a>
    : <button className="btn primary" disabled={busy} onClick={start}>{busy ? "正在提取…" : "提取"}</button>}>
    {pdf ? <input aria-label="提取页码" placeholder="页码，例如 1-5,85" value={range} onChange={(e) => setRange(e.target.value)} disabled={busy} /> : null}
    {error ? <p role="alert" className="error">{error}</p> : null}
    {result && result.status !== 'done' ? <p role="alert" className="error">文字提取未完成，请稍后重试</p> : null}
  </Modal>;
}
