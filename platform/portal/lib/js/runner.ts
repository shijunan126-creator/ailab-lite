// The page side of browser JavaScript: one worker, created on first use (the engine is fetched only then), jobs run
// one at a time. QuickJS stops a program at its own deadline; if the worker does not answer shortly after that,
// it is terminated and the next job starts a fresh one, so a stuck program never holds the page or the queue.

import { DEFAULT_LIMITS, type JsJob, type JsReply } from "./protocol";

const GRACE_MS = 1000; // after the program's own deadline
const COLD_MS = 4000; // first job: also fetching and compiling the engine

let worker: Worker | null = null;
let warm = false;
let tail: Promise<unknown> = Promise.resolve();

function spawn(): Worker {
  return new Worker(new URL("./worker.ts", import.meta.url), { type: "module", name: "ailab-js" });
}

function once(job: JsJob): Promise<JsReply> {
  return new Promise((resolve) => {
    let w: Worker;
    try {
      w = worker ??= spawn();
    } catch (error) {
      resolve({ id: job.id, ok: false, error: { type: "unavailable", message: `浏览器不支持后台执行：${String((error as Error)?.message || error).slice(0, 200)}` }, stats: { ms: 0 } });
      return;
    }
    const started = performance.now();
    const finish = (reply: JsReply) => {
      clearTimeout(timer);
      w.removeEventListener("message", onMessage);
      w.removeEventListener("error", onError);
      resolve(reply);
    };
    const kill = (message: string, type: "timeout" | "unavailable") => {
      w.terminate();
      if (worker === w) {
        worker = null;
        warm = false;
      }
      finish({ id: job.id, ok: false, error: { type, message }, stats: { ms: Math.round(performance.now() - started) } });
    };
    const onMessage = (e: MessageEvent<JsReply>) => {
      if (e.data?.id !== job.id) return;
      warm = e.data.error?.type !== "unavailable";
      finish(e.data);
    };
    const onError = (e: ErrorEvent) => {
      e.preventDefault();
      kill(`JavaScript 引擎出错：${e.message || "无法加载"}`, "unavailable");
    };
    const timer = setTimeout(() => kill("运行超时", "timeout"), job.timeout_ms + GRACE_MS + (warm ? 0 : COLD_MS));
    w.addEventListener("message", onMessage);
    w.addEventListener("error", onError);
    w.postMessage(job);
  });
}

/** Run a program; resolves with its result or a classified error, never rejects. */
export function runJs(job: Omit<JsJob, "id" | "limits"> & { limits?: Partial<JsJob["limits"]> }): Promise<JsReply> {
  const full: JsJob = { ...job, id: crypto.randomUUID(), limits: { ...DEFAULT_LIMITS, ...job.limits } };
  const next = tail.then(() => once(full));
  tail = next.catch(() => undefined);
  return next;
}
