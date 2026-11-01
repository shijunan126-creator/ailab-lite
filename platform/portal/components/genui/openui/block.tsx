"use client";

// The only place that renders OpenUI programs. Loaded on demand with the first UI block of a page.
import { useEffect, useMemo, useRef, useState } from "react";
import { createStore, evaluateRoot } from "@openuidev/lang-core";
import { Renderer, createParser, type ActionEvent, type ParseResult } from "@openuidev/react-lang";
import { library } from "../catalog/components";
import { toProgram } from "../catalog/program";
import { COMPONENT_NAMES } from "../catalog/limits";
import { componentsOf, nodeToMarkdown } from "../catalog/schema";
import { BlockPhaseProvider, type BlockPhase, useBlockStates, useGenUIHost } from "../host";
import { registerMarkdown } from "../markdown-bridge";
import { report } from "../telemetry";

const parser = createParser(library.toJSONSchema(), "Stack");
const parse = (program: string): ParseResult | null => {
  try {
    return parser.parse(program);
  } catch {
    return null;
  }
};
const LAYOUT = new Set(["Stack", "Grid", "Card", "Tabs", "Details"]);
/** A tree that shows something. A program can parse yet resolve to layout with nothing in it (statements the parser
 * dropped, references to nothing): that is a failed block, shown as one, never as blank space. */
function shows(parsed: ParseResult | null): boolean {
  const visit = (value: unknown): boolean => {
    if (Array.isArray(value)) return value.some(visit);
    if (!value || typeof value !== "object" || (value as { type?: unknown }).type !== "element") return false;
    const node = value as { typeName: string; hasDynamicProps?: boolean; props?: Record<string, unknown> };
    // children built by an expression (@Each, a ternary) are only known once evaluated
    return !LAYOUT.has(node.typeName) || !!node.hasDynamicProps || Object.values(node.props ?? {}).some(visit);
  };
  return visit(parsed?.root);
}

/** Components that do not make a block worth a picture: layout, controls (a row of buttons is a way to answer),
 * plain text, and what copies as itself instead: tables as tables, code as text. */
const NO_PICTURE = new Set([...LAYOUT, "Tab", "Divider", "Heading", "Text", "Table", "Compare", "Code", "Diff", "JsonView", "Select", "Slider", "Toggle", "Input", "Button"]);
/** Whether a block is offered as a picture. Components built by an expression (@Each, a ternary) are still AST
 * nodes in the parsed tree and count by name. A generated page runs in its own frame, which cannot be drawn. */
export function pictured(parsed: ParseResult | null): boolean {
  const names = new Set<string>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) return value.forEach(visit);
    if (!value || typeof value !== "object") return;
    const node = value as { type?: unknown; typeName?: unknown; k?: unknown; name?: unknown };
    if (node.type === "element" && typeof node.typeName === "string") names.add(node.typeName);
    else if (node.k === "Comp" && typeof node.name === "string") names.add(node.name);
    Object.values(value).forEach(visit);
  };
  visit(parsed?.root);
  if (names.has("HtmlArtifact")) return false;
  return [...names].some((name) => COMPONENT_NAMES.includes(name) && !NO_PICTURE.has(name));
}

const unwrap = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) && "value" in v ? (v as { value: unknown }).value : v);

/** The block as Markdown, from its evaluated tree: rows built with @Each, sums and `$variable` results are values
 * there, and the parsed tree only holds their expressions. Seeded the way the Renderer seeds its store. */
export function sourceToMarkdown(source: string, state?: Record<string, unknown>): string {
  const parsed = parse(toProgram(source));
  if (!shows(parsed)) return "";
  const store = createStore();
  try {
    const bound: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(state ?? {})) if (key.startsWith("$")) bound[key] = value;
    else store.set(key, value);
    store.initialize(parsed.stateDeclarations ?? {}, bound);
    const read = (name: string) => unwrap(store.get(name));
    return nodeToMarkdown(evaluateRoot(parsed, { library, store, ctx: { getState: read, resolveRef: () => undefined } }), read);
  } catch {
    return "```openui\n" + source.trimEnd() + "\n```";
  } finally {
    store.dispose();
  }
}
registerMarkdown(sourceToMarkdown);

export type BlockState = "ok" | "empty";

export default function OpenUIBlock({ source, index, streaming, phase, onState }: { source: string; index: number; streaming: boolean; phase: BlockPhase; onState: (state: BlockState, picture: boolean) => void }) {
  const host = useGenUIHost();
  const states = useBlockStates();
  // read once: later saves must not re-seed the store the reader is changing
  const [initialState] = useState(() => states?.initial(index));
  const onStateUpdate = useMemo(() => (states?.save ? (snapshot: Record<string, unknown>) => states.save!(index, snapshot) : undefined), [states, index]);
  const program = useMemo(() => toProgram(source), [source]);
  const parsed = useMemo(() => parse(program), [program]);
  // Last valid tree: a source that briefly fails to parse mid-stream keeps showing what parsed before.
  const good = useRef<string | null>(null);
  if (shows(parsed)) good.current = program;
  const shown = shows(parsed) ? program : streaming ? good.current : null;
  const started = useRef(performance.now());
  const reported = useRef(false);

  useEffect(() => {
    onState(shown ? "ok" : "empty", !!shown && pictured(shown === program ? parsed : parse(shown)));
    if (streaming || reported.current) return;
    reported.current = true;
    if (shown) {
      const names = componentsOf(parsed?.root ?? null);
      report("rendered", names[0], performance.now() - started.current);
      for (const name of new Set(names.slice(1))) report("rendered", name);
    } else report("fallback");
  }, [streaming, shown, program, parsed, onState]);

  if (!shown) return null;
  return (
    <BlockPhaseProvider value={phase}>
      <Renderer
        response={shown}
        library={library}
        isStreaming={streaming}
        publishObservability={false}
        initialState={initialState}
        onStateUpdate={onStateUpdate}
        onError={(errors) => {
          if (!streaming && errors.length) report("error");
        }}
        onAction={(event: ActionEvent) => {
          // only @ToAssistant is open in v1; anything else is refused and counted
          if (event.type !== "continue_conversation" || !event.humanFriendlyMessage || host.readOnly || !host.send) return report("error");
          report("action");
          void host.send(event.humanFriendlyMessage);
        }}
      />
    </BlockPhaseProvider>
  );
}
