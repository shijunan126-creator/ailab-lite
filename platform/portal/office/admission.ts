import { getHost } from "./host";

/** One request owns a disposable Worker and the transferred source buffer. */
export async function admit(bytes: ArrayBuffer, kind: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  if (kind === "pdf") return bytes;
  signal?.throwIfAborted();
  const code = await getHost().request<string>({t: "asset", name: "admission-worker.js", binary: false});
  signal?.throwIfAborted();
  const url = URL.createObjectURL(new Blob([code], {type: "text/javascript"}));
  let worker: Worker | null = null;
  try {
    worker = new Worker(url);
    return await new Promise<ArrayBuffer>((resolve, reject) => {
      const abort = () => { clean(); reject(new Error("文档准备已取消")); };
      const timer = setTimeout(() => { clean(); reject(new Error("浏览器文档准备超时")); }, 10000);
      const clean = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); if (worker) worker.onmessage = worker.onerror = null; };
      signal?.addEventListener("abort", abort, {once: true});
      worker!.onerror = () => { clean(); reject(new Error("浏览器文档准备失败")); };
      worker!.onmessage = event => { clean(); if (event.data.ok && event.data.bytes instanceof ArrayBuffer) resolve(event.data.bytes); else reject(new Error(event.data.error ?? "浏览器文档超限")); };
      worker!.postMessage({ bytes, kind }, [bytes]);
    });
  } finally {
    worker?.terminate();
    URL.revokeObjectURL(url);
  }
}
