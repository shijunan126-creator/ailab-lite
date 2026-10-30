/// <reference lib="webworker" />
// The worker the page starts for browser JavaScript (lib/js/runner.ts): one job at a time, see engine.ts.

import { dropEngine, loadEngine, run } from "./engine";
import type { JsJob, JsReply } from "./protocol";

self.onmessage = async (event: MessageEvent<JsJob>) => {
  const job = event.data;
  try {
    const reply = run(await loadEngine(job.limits.memory_mb), job);
    if (reply.error?.type === "memory") dropEngine();
    (self as unknown as Worker).postMessage(reply);
  } catch (error) {
    const reply: JsReply = { id: job.id, ok: false, error: { type: "unavailable", message: `无法启动 JavaScript 引擎：${String((error as Error)?.message || error).slice(0, 200)}` }, stats: { ms: 0 } };
    (self as unknown as Worker).postMessage(reply);
  }
};
