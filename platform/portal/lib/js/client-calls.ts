// The model asked this browser to run a small program (contract §4.2). Whichever open tab claims the call first runs
// it and reports back; a call that is too late, already taken or no longer wanted is left alone. The result is the
// browser's own computation: the API treats it as data from the user's side, never as an instruction.

import { api } from "../api";
import { captureView, hasView } from "../office-views";
import { runJs } from "./runner";
import type { JsLimits } from "./protocol";

export type ClientCall = { call_id: string; kind: string; code?: string; input?: unknown; timeout_ms: number; limits?: Partial<JsLimits>; deadline?: string };

/** A viewport capture is at most this large (base64 of the PNG); a bigger one is retaken narrower. */
const MAX_CAPTURE_CHARS = Math.floor((2 * 1024 * 1024 * 4) / 3);

const seen = new Set<string>(); // per page: a reconnecting stream replays the event

export async function handleClientCall(runId: string, call: ClientCall): Promise<void> {
  const key = `${runId}:${call.call_id}`;
  const capture = call.kind === "capture_view" ? (call.input as { view_id?: unknown; at?: unknown; max_width?: unknown } | undefined) : undefined;
  if (seen.has(key)) return;
  if (call.kind === "js" ? typeof call.code !== "string" : !(typeof capture?.view_id === "string" && hasView(capture.view_id))) return;
  seen.add(key);
  if (call.deadline && Date.parse(call.deadline) < Date.now()) return;
  let token: string;
  try {
    token = (await api<{ claim_token: string }>(`/v1/runs/${runId}/client-calls/${encodeURIComponent(call.call_id)}/claim`, { method: "POST" })).claim_token;
  } catch {
    return; // another tab has it, or it expired
  }
  let body: Record<string, unknown>;
  if (capture) {
    // the run stops waiting at its deadline (else after timeout_ms); a reply after that is wasted
    const until = call.deadline ? Date.parse(call.deadline) : Date.now() + call.timeout_ms;
    body = { claim_token: token, ...(await takeCapture(capture.view_id as string, capture.at, capture.max_width, until)) };
  } else {
    const reply = await runJs({ code: call.code!, input: call.input, timeout_ms: call.timeout_ms, limits: call.limits });
    body = reply.ok
      ? { claim_token: token, ok: true, value: reply.value, logs: reply.logs, stats: reply.stats }
      : { claim_token: token, ok: false, logs: reply.logs, error: reply.error, stats: reply.stats };
  }
  try {
    await api(`/v1/runs/${runId}/client-calls/${encodeURIComponent(call.call_id)}/result`, { method: "POST", body: JSON.stringify(body) });
  } catch {
    // too late (the run moved on or the call expired): the agent already told the model it was unavailable
  }
}

async function takeCapture(viewId: string, at: unknown, maxWidth: unknown, until: number): Promise<{ ok: true; value: unknown } | { ok: false; error: string }> {
  let width = typeof maxWidth === "number" && maxWidth >= 320 ? Math.min(Math.floor(maxWidth), 2400) : 1600;
  try {
    for (;;) {
      const left = until - Date.now();
      if (left <= 0) return { ok: false, error: "截图超时" };
      const pending = captureView(viewId, typeof at === "string" && at ? at : null, width);
      let timer: ReturnType<typeof setTimeout> | undefined;
      const shot = pending && (await Promise.race([pending, new Promise<never>((_, reject) => (timer = setTimeout(() => reject(new Error("截图超时")), left)))]).finally(() => clearTimeout(timer)));
      if (!shot) return { ok: false, error: "查看器已关闭" };
      if (shot.data.length <= MAX_CAPTURE_CHARS) return { ok: true, value: { image: shot.data, mime_type: "image/png", width: shot.width, height: shot.height, at: shot.at, version: shot.version } };
      if (width <= 480) return { ok: false, error: "截图过大" };
      width = Math.max(480, Math.floor(width * 0.6));
    }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "截图失败" };
  }
}
