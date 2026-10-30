// The model asked this browser to run a small program (contract §4.2). Whichever open tab claims the call first runs
// it and reports back; a call that is too late, already taken or no longer wanted is left alone. The result is the
// browser's own computation: the API treats it as data from the user's side, never as an instruction.

import { api } from "../api";
import { runJs } from "./runner";
import type { JsLimits } from "./protocol";

export type ClientCall = { call_id: string; kind: string; code: string; input?: unknown; timeout_ms: number; limits?: Partial<JsLimits>; deadline?: string };

const seen = new Set<string>(); // per page: a reconnecting stream replays the event

export async function handleClientCall(runId: string, call: ClientCall): Promise<void> {
  const key = `${runId}:${call.call_id}`;
  if (call.kind !== "js" || seen.has(key)) return;
  seen.add(key);
  if (call.deadline && Date.parse(call.deadline) < Date.now()) return;
  let token: string;
  try {
    token = (await api<{ claim_token: string }>(`/v1/runs/${runId}/client-calls/${encodeURIComponent(call.call_id)}/claim`, { method: "POST" })).claim_token;
  } catch {
    return; // another tab has it, or it expired
  }
  const reply = await runJs({ code: call.code, input: call.input, timeout_ms: call.timeout_ms, limits: call.limits });
  const body = reply.ok
    ? { claim_token: token, ok: true, value: reply.value, logs: reply.logs, stats: reply.stats }
    : { claim_token: token, ok: false, logs: reply.logs, error: reply.error, stats: reply.stats };
  try {
    await api(`/v1/runs/${runId}/client-calls/${encodeURIComponent(call.call_id)}/result`, { method: "POST", body: JSON.stringify(body) });
  } catch {
    // too late (the run moved on or the call expired): the agent already told the model it was unavailable
  }
}
