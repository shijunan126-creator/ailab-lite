// Runs one small JavaScript program in QuickJS (WebAssembly): no network, no files, no DOM, no host objects — only
// `input` and `console.log`. QuickJS itself stops the program at the deadline (interrupt handler), at the memory
// limit and at the stack limit; the page terminates the worker this runs in if even that fails. The result is the
// program's completion value (its last expression), as JSON.

import variant from "@jitl/quickjs-wasmfile-release-sync";
import { newQuickJSWASMModuleFromVariant, newVariant, shouldInterruptAfterDeadline, type QuickJSContext, type QuickJSHandle, type QuickJSWASMModule } from "quickjs-emscripten-core";
import type { JsError, JsJob, JsReply } from "./protocol";


const failed = <S, F>(r: { value?: S; error?: F }): r is { error: F } => r.error !== undefined;

const MAX_LOG = 8 * 1024;

function classify(error: unknown, timedOut: boolean): JsError {
  const e = (error && typeof error === "object" ? error : { message: String(error) }) as { name?: string; message?: string };
  const message = String(e.message || e.name || error || "执行出错").slice(0, 1000);
  if (timedOut || /interrupted/i.test(message)) return { type: "timeout", message: "运行超时" };
  if (/out of memory/i.test(message)) return { type: "memory", message: "超出内存限制" };
  if (e.name === "SyntaxError") return { type: "syntax", message: `SyntaxError: ${message}` };
  return { type: "runtime", message: e.name && e.name !== "Error" ? `${e.name}: ${message}` : message };
}

export function run(engineModule: QuickJSWASMModule, job: JsJob): JsReply {
  const started = performance.now();
  const runtime = engineModule.newRuntime();
  runtime.setMemoryLimit(job.limits.memory_mb * 1024 * 1024); // advisory here; the hard cap is in loadEngine
  runtime.setMaxStackSize(job.limits.stack_kb * 1024);
  const deadline = Date.now() + job.timeout_ms;
  runtime.setInterruptHandler(shouldInterruptAfterDeadline(deadline));
  const vm: QuickJSContext = runtime.newContext();
  const logs: string[] = [];
  let logSize = 0;
  const reply = (r: Omit<JsReply, "id" | "stats">): JsReply => ({ ...r, id: job.id, logs: logs.join("\n") || undefined, stats: { ms: Math.round(performance.now() - started), heap_kb: heapKb() } });
  const heapKb = () => {
    try {
      const usage = runtime.computeMemoryUsage();
      const total = vm.getNumber(vm.getProp(usage, "memory_used_size"));
      usage.dispose();
      return Math.round(total / 1024);
    } catch {
      return undefined;
    }
  };
  try {
    const consoleObj = vm.newObject();
    const log = vm.newFunction("log", (...args: QuickJSHandle[]) => {
      const line = args
        .map((a) => {
          const v = vm.dump(a);
          return typeof v === "string" ? v : JSON.stringify(v);
        })
        .join(" ");
      if (logSize < MAX_LOG) {
        logs.push(line.slice(0, MAX_LOG - logSize));
        logSize += line.length + 1;
      }
    });
    for (const name of ["log", "info", "warn", "error"]) vm.setProp(consoleObj, name, log);
    vm.setProp(vm.global, "console", consoleObj);
    log.dispose();
    consoleObj.dispose();
    // input arrives as JSON text and is parsed inside the VM: no host object ever crosses over
    const raw = vm.newString(JSON.stringify(job.input ?? null));
    vm.setProp(vm.global, "__input", raw);
    raw.dispose();
    vm.unwrapResult(vm.evalCode("globalThis.input = JSON.parse(globalThis.__input); delete globalThis.__input;")).dispose();

    const evaluated = vm.evalCode(job.code, "main.js");
    if (failed(evaluated)) {
      const err = vm.dump(evaluated.error);
      evaluated.error.dispose();
      return reply({ ok: false, error: classify(err, Date.now() > deadline) });
    }
    let value = evaluated.value;
    // an async program: run its jobs until the promise settles (still bounded by the interrupt handler)
    for (let i = 0; i < 10000 && vm.getPromiseState(value).type === "pending"; i++) {
      const jobs = runtime.executePendingJobs();
      if (failed(jobs)) {
        const err = vm.dump(jobs.error);
        jobs.error.dispose();
        value.dispose();
        return reply({ ok: false, error: classify(err, Date.now() > deadline) });
      }
      if (jobs.value === 0) break;
    }
    const state = vm.getPromiseState(value);
    if (state.type === "rejected") {
      const err = vm.dump(state.error);
      state.error.dispose();
      value.dispose();
      return reply({ ok: false, error: classify(err, false) });
    }
    if (state.type === "pending") {
      value.dispose();
      return reply({ ok: false, error: { type: "runtime", message: "Promise 没有完成" } });
    }
    if (!state.notAPromise) {
      value.dispose();
      value = state.value;
    }
    // the completion value of the program, serialized inside the VM
    const json = vm.getProp(vm.global, "JSON");
    const stringify = vm.getProp(json, "stringify");
    const text = vm.callFunction(stringify, json, value);
    stringify.dispose();
    json.dispose();
    value.dispose();
    if (failed(text)) {
      const err = vm.dump(text.error);
      text.error.dispose();
      return reply({ ok: false, error: classify(err, Date.now() > deadline) });
    }
    const out = vm.typeof(text.value) === "string" ? vm.getString(text.value) : undefined;
    text.value.dispose();
    if (out !== undefined && new TextEncoder().encode(out).length > job.limits.output_kb * 1024) {
      return reply({ ok: false, error: { type: "output_limit", message: `结果超过 ${job.limits.output_kb} KB` } });
    }
    return reply({ ok: true, value: out });
  } catch (error) {
    return reply({ ok: false, error: classify(error, Date.now() > deadline) });
  } finally {
    try {
      vm.dispose();
      runtime.dispose();
    } catch {
      // a program stopped at the memory limit can leave the runtime unusable; the next job gets a fresh one
    }
  }
}

let engine: { memoryMb: number; module: Promise<QuickJSWASMModule> } | null = null;

const PAGE = 64 * 1024;
const BASE_PAGES = 256; // the 16 MiB the engine starts with

/**
 * The engine, loaded once per worker; a failed load is retried by the next job. QuickJS's own memory limit is not
 * enforced in this build (no malloc_usable_size), so the cap is the WebAssembly memory itself: it cannot grow past
 * the start size plus the job's limit, and an allocation beyond that fails inside QuickJS as "out of memory".
 */
export function loadEngine(memoryMb = 32): Promise<QuickJSWASMModule> {
  if (engine?.memoryMb !== memoryMb) {
    const wasmMemory = new WebAssembly.Memory({ initial: BASE_PAGES, maximum: BASE_PAGES + (memoryMb * 1024 * 1024) / PAGE });
    engine = { memoryMb, module: newQuickJSWASMModuleFromVariant(newVariant(variant, { wasmMemory })) };
  }
  const current = engine;
  current.module.catch(() => {
    if (engine === current) engine = null;
  });
  return current.module;
}

/** After running out of memory the engine's heap may be damaged: the next job starts a new one. */
export function dropEngine() {
  engine = null;
}
