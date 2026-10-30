// What the page and the QuickJS worker exchange; the result is what the page reports to the API (contract §4.2).

export type JsLimits = { memory_mb: number; stack_kb: number; output_kb: number };
export type JsJob = { id: string; code: string; input?: unknown; timeout_ms: number; limits: JsLimits };
export type JsErrorType = "timeout" | "memory" | "syntax" | "runtime" | "output_limit" | "unavailable";
export type JsError = { type: JsErrorType; message: string };
export type JsReply = {
  id: string;
  ok: boolean;
  value?: string; // JSON text of the program's completion value; absent when it was undefined
  logs?: string;
  error?: JsError;
  stats: { ms: number; heap_kb?: number };
};

export const DEFAULT_LIMITS: JsLimits = { memory_mb: 32, stack_kb: 512, output_kb: 64 };
