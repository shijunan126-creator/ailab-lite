"use client";

// HtmlArtifact (HLD §6.16, P4-H): a page the model wrote in HTML/CSS/JS, for what the components cannot draw.
// It runs only in the sandboxed frame route: an opaque origin with no network, no cookies, no storage and no bridge
// back to the conversation beyond its height. While the answer streams, the markup shows with every script off
// (genui-preview); once the fence has closed the complete page runs (genui-run); an answer that ended before the
// fence closed never runs it. A page that scrolls far out of view is taken down and rebuilt on return, so history
// never keeps timers or animations alive.
import { useEffect, useRef, useState } from "react";
import { useIsStreaming } from "@openuidev/react-lang";
import { API_BASE } from "../../../lib/api";
import { RUN_HELPER } from "../../../lib/frame";
import { IconCode, IconRetry } from "../../icons";
import { SandboxFrame } from "../../sandbox-frame";
import { useBlockPhase } from "../host";

const MIN_H = 120;
const PREVIEW_EVERY = 300; // ms between repaints of a page still being written

// Whether the server admits generated pages (`features.genui_html`); asked once per page load.
let admitted: Promise<boolean> | null = null;
function useAdmitted(): boolean | null {
  const [value, setValue] = useState<boolean | null>(null);
  useEffect(() => {
    admitted ??= fetch(`${API_BASE}/v1/auth/me`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => me?.features?.genui_html === true)
      .catch(() => false);
    let stale = false;
    admitted.then((v) => !stale && setValue(v));
    return () => {
      stale = true;
    };
  }, []);
  return value;
}

const TOKENS: [string, string][] = [
  ["--bg", "--background"], ["--surface", "--subtle"], ["--fg", "--foreground"], ["--muted", "--muted-foreground"],
  ["--border", "--border"], ["--accent", "--primary"], ["--accent-fg", "--primary-foreground"],
  ...[1, 2, 3, 4, 5, 6].map((i): [string, string] => [`--series-${i}`, `--series-${i}`]),
];

/** The host's colours and type as the variables a generated page is told to use, so it matches light and dark. */
function themeCss(): string {
  const root = getComputedStyle(document.documentElement);
  const vars = TOKENS.map(([name, from]) => `${name}:${root.getPropertyValue(from).trim()}`).join(";");
  const dark = document.documentElement.classList.contains("dark");
  return `:root{color-scheme:${dark ? "dark" : "light"};${vars}}html{font:15px/1.6 ${getComputedStyle(document.body).fontFamily};color:var(--fg);background:transparent;-webkit-text-size-adjust:100%}body{margin:0}`;
}

function useTheme(): string {
  const [css, setCss] = useState("");
  useEffect(() => {
    setCss(themeCss());
    const watch = new MutationObserver(() => setCss(themeCss()));
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => watch.disconnect();
  }, []);
  return css;
}

/** The theme first (the page's own CSS overrides it), then, for a running page, the height reporter. */
function compose(doc: string, theme: string, run: boolean): string {
  const style = `<style id="ailab-theme">${theme}</style>`;
  const withTheme = /<head[\s>]/i.test(doc) ? doc.replace(/<head[^>]*>/i, (m) => m + style) : style + doc;
  return run ? withTheme + RUN_HELPER : withTheme;
}

/** True while the element is within a screen or so of the viewport. */
function useNear<T extends HTMLElement>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [near, setNear] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), { rootMargin: "100% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, near];
}

/** A value that follows `value` at most every `ms` while `live`, and at once when it stops. */
function useThrottled(value: string, ms: number, live: boolean): string {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    if (!live) return setShown(value);
    const wait = Math.max(0, last.current + ms - Date.now());
    const t = setTimeout(() => {
      last.current = Date.now();
      setShown(value);
    }, wait);
    return () => clearTimeout(t);
  }, [value, ms, live]);
  return shown;
}

export function HtmlArtifactView({ title, document: doc, fallbackText }: { title: string; document: string; fallbackText: string }) {
  const streaming = useIsStreaming();
  const phase = useBlockPhase();
  const complete = phase === "complete" && !streaming;
  const admittedNow = useAdmitted();
  const theme = useTheme();
  const [wrap, near] = useNear<HTMLDivElement>();
  const [height, setHeight] = useState(0);
  const [running, setRunning] = useState(false); // the run frame has reported in: the preview can go
  const [left, setLeft] = useState(false);
  const [source, setSource] = useState(false);
  const [round, setRound] = useState(0);
  const preview = useThrottled(doc, PREVIEW_EVERY, !complete);

  useEffect(() => setRunning(false), [round, near]);

  const cap = typeof window === "undefined" ? 900 : Math.max(320, Math.min(1000, Math.round(window.innerHeight * 0.85)));
  const shownHeight = Math.min(cap, Math.max(MIN_H, height || 240));
  const onHeight = (h: number) => {
    setHeight(h);
    setRunning(true);
  };

  let body: React.ReactNode;
  if (admittedNow === false) body = <p className="gu-html-note">{fallbackText || "此页面未开放运行"}</p>;
  else if (phase === "cut") body = <p className="gu-html-note">页面没有写完，未运行</p>;
  else if (left)
    body = (
      <div className="gu-html-note">
        <span>页面尝试打开其它地址，已停止运行</span>
        <button type="button" className="link" onClick={() => { setLeft(false); setRound((n) => n + 1); }}>重新运行</button>
      </div>
    );
  else if (!doc.trim() || theme === "" || admittedNow === null) body = <div className="genui-skeleton" />;
  else if (!near) body = null; // out of view: nothing runs; the box keeps its height
  else
    body = (
      <>
        {!complete || !running ? (
          <SandboxFrame kind="genui-preview" html={compose(complete ? doc : preview, theme, false)} title={title} className="gu-html-frame"
            style={{ height: shownHeight }} onHeight={setHeight} onLeave={() => setLeft(true)} />
        ) : null}
        {complete ? (
          <SandboxFrame key={round} kind="genui-run" html={compose(doc, theme, true)} title={title} theme={theme}
            className={running ? "gu-html-frame" : "gu-html-frame gu-html-pending"} style={{ height: shownHeight }}
            onHeight={onHeight} onLeave={() => setLeft(true)} />
        ) : null}
      </>
    );

  return (
    <figure className="gu-html" data-state={complete ? "complete" : phase === "cut" ? "cut" : "writing"}>
      <figcaption className="gu-html-bar">
        <span className="gu-html-title">{title}</span>
        {complete && admittedNow ? (
          <button type="button" className="icon-btn" aria-label="重新运行" title="重新运行" onClick={() => { setLeft(false); setRound((n) => n + 1); }}>
            <IconRetry size={15} />
          </button>
        ) : null}
        <button type="button" className="icon-btn" aria-label="查看源码" title="源码" aria-pressed={source} onClick={() => setSource((v) => !v)}>
          <IconCode size={15} />
        </button>
      </figcaption>
      <div ref={wrap} className="gu-html-body" style={{ minHeight: body === null ? shownHeight : undefined }}>{body}</div>
      {source ? <pre className="md-code gu-html-source"><code>{doc}</code></pre> : null}
      {/* paper cannot run a page: print shows what it says */}
      <p className="gu-html-print">{fallbackText}</p>
    </figure>
  );
}
