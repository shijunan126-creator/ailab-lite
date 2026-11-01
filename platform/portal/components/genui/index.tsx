"use client";

// <GenUIBlock/>: the one entry point for a generated UI block (HLD §6.5). The renderer, catalog and charts load
// with the first block on the page; a block that cannot render falls back without affecting the rest of the answer.
import { Component, lazy, Suspense, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { useTouchReveal } from "../../lib/use-touch-reveal";
import { MAX_BLOCK_CHARS } from "./catalog/limits";
import { ImageTools, placeTools } from "./image";
import type { BlockState } from "./openui/block";

/** Loads the GenUI chunk ahead of time (print waits for it so every block is on paper). */
export const preloadGenUI = () => Promise.all([import("./openui/block"), import("./catalog/components").then((m) => m.preloadParts())]);

class Boundary extends Component<{ fallback: ReactNode; children: ReactNode; resetKey: string }, { failed: boolean; key: string }> {
  state = { failed: false, key: this.props.resetKey };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  static getDerivedStateFromProps(props: { resetKey: string }, state: { failed: boolean; key: string }) {
    // a failed load is retried when the block changes (and by the retry button, which changes the key)
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null;
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function Skeleton() {
  return <div className="genui-skeleton" aria-label="界面生成中" role="status" />;
}

function SourceFallback({ source, onRetry }: { source: string; onRetry?: () => void }) {
  return (
    <div className="genui-fallback">
      <span>无法显示此内容</span>
      {onRetry ? <button type="button" className="link" onClick={onRetry}>重试</button> : null}
      <details>
        <summary>查看源码</summary>
        <pre className="md-code"><code>{source}</code></pre>
      </details>
    </div>
  );
}

/** `index`: the block's place among the answer's UI blocks, the key its saved state is kept under. */
export function GenUIBlock({ source, index, streaming, closed }: { source: string; index: number; streaming: boolean; closed: boolean }) {
  const [state, setState] = useState<BlockState | null>(null);
  const [picture, setPicture] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const [touched, reveal] = useTouchReveal(ref);
  const onState = useCallback((next: BlockState, pictured: boolean) => {
    setState(next);
    setPicture(pictured);
  }, []);
  // a new lazy component per attempt: React caches a failed import, a retry must load again
  const OpenUIBlock = useMemo(() => lazy(() => import("./openui/block")), [attempt]);
  if (source.length > MAX_BLOCK_CHARS) return <SourceFallback source={source.slice(0, 2000) + "\n…"} />;
  const live = streaming && !closed;
  const failed = !live && state === "empty";
  return (
    <div ref={ref} className="genui-block" data-state={live ? "streaming" : state || "loading"} data-touched={touched || undefined}
      onPointerEnter={() => { if (picture && ref.current) placeTools(ref.current); }}
      onPointerDown={(e) => {
        if (picture && ref.current && reveal(e)) placeTools(ref.current);
      }}>
      <Boundary resetKey={String(attempt)} fallback={<SourceFallback source={source} onRetry={() => setAttempt((n) => n + 1)} />}>
        <Suspense fallback={<Skeleton />}>
          {failed ? <SourceFallback source={source} /> : null}
          <div hidden={failed}>
            <OpenUIBlock source={source} index={index} streaming={live} phase={live ? "writing" : closed ? "complete" : "cut"} onState={onState} />
          </div>
          {live && state !== "ok" ? <Skeleton /> : null}
        </Suspense>
      </Boundary>
      {!live && state === "ok" && picture ? <ImageTools block={ref} /> : null}
    </div>
  );
}
