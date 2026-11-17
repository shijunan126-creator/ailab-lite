// Typeset TeX. KaTeX (about 75 KB gzipped) loads with the first formula, not with the chat; until then the source
// stands in. Its stylesheet is global (app/globals.css) so it sits in the cascade layers and fonts load only when used.
"use client";

import { memo, useEffect, useSyncExternalStore } from "react";
import type { KatexOptions } from "katex";

type Katex = typeof import("katex").default;

let katex: Katex | null = null;
let chem = false;
let loading: Promise<void> | null = null;
let chemLoading: Promise<void> | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((f) => f());

// \ce and \pu (chemistry) come from mhchem, loaded only for answers that use them
const CHEM = /\\(ce|pu)\s*\{/;

function load(tex: string) {
  loading ??= Promise.all([import("katex"), import("katex/contrib/copy-tex")])
    .then(([m]) => {
      katex = m.default;
      changed();
    })
    .catch(() => {
      loading = null; // a failed chunk (offline, a new deploy) is retried by the next formula
    });
  if (CHEM.test(tex))
    chemLoading ??= loading
      .then(() => import("katex/contrib/mhchem"))
      .then(() => {
        chem = true;
        cache.clear();
        changed();
      })
      .catch(() => {
        chemLoading = null;
      });
}

const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => listeners.delete(f);
};
const ready = () => (katex ? (chem ? 2 : 1) : 0);

const OPTIONS: KatexOptions = {
  throwOnError: true,
  // Chinese inside math, \\ outside an environment and other forgivable habits typeset instead of failing
  strict: "ignore",
  // \href, \url, \includegraphics, \htmlClass…: model output never gets links, images or markup through TeX
  trust: false,
  maxSize: 20,
  maxExpand: 1000,
  output: "htmlAndMathml",
};

type Rendered = { html: string } | { error: string };

// A streamed answer re-renders its formulas on every tick; typesetting each source once keeps that cheap.
const cache = new Map<string, Rendered>();
const CACHE_MAX = 600;

function typeset(tex: string, display: boolean): Rendered {
  const key = (display ? "D" : "I") + tex;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return hit;
  }
  let out: Rendered;
  try {
    out = { html: katex!.renderToString(tex, { ...OPTIONS, displayMode: display }) };
  } catch (e) {
    out = { error: e instanceof Error ? e.message : String(e) };
  }
  cache.set(key, out);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value!);
  return out;
}

type Props = {
  tex: string;
  /** Typeset as a display formula (centred, full size). */
  display?: boolean;
  /** A block of its own rather than a formula inside a line of text. */
  block?: boolean;
  /** The source as written, delimiters included: shown while KaTeX loads or when the TeX does not parse. */
  raw: string;
};

export default memo(function TexMath({ tex, display = false, block = false, raw }: Props) {
  const level = useSyncExternalStore(subscribe, ready, () => 0);
  useEffect(() => {
    if (!katex || (!chem && CHEM.test(tex))) load(tex);
  }, [tex, level]);
  const Tag = block ? "div" : "span";
  const cls = `md-math${display ? " display" : ""}`;
  if (!level || (!chem && CHEM.test(tex))) return <Tag className={`${cls} source`}>{raw}</Tag>;
  const out = typeset(tex, display);
  if ("error" in out) return <Tag className={`${cls} error`} title={out.error}>{raw}</Tag>;
  return <Tag className={cls} dangerouslySetInnerHTML={{ __html: out.html }} />;
});

/** A display block whose closing delimiter has not streamed in yet. */
export function PendingMath() {
  return <div className="md-math display pending" aria-hidden="true" />;
}
