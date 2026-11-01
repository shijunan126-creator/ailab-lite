"use client";

// A rendered block as a picture: copied to the clipboard or saved as PNG. The block is drawn from the page as the
// reader sees it (theme, tab, slider position, scrolled table), framed by a margin of the page's own background.
// The rasterizer loads on the first click; a block never pays for it until then.
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { IconCheck, IconCopy, IconDownload, IconX } from "../icons";

/** Margin around the block in the picture, CSS px. */
const MARGIN = 20;
/** Largest canvas every current browser allocates (Safari's area limit is 16,777,216 px; sides stay under 16,384). */
const MAX_AREA = 16_000_000;
const MAX_SIDE = 16_000;

/** Device pixels per CSS px: sharp on paper and in documents (at least 2), never past what the canvas can hold. */
export function fitScale(width: number, height: number, preferred: number): number {
  const scale = Math.min(Math.max(2, preferred), 3);
  const limit = Math.min(Math.sqrt(MAX_AREA / Math.max(1, width * height)), MAX_SIDE / Math.max(1, width), MAX_SIDE / Math.max(1, height));
  return Math.max(0.1, Math.min(scale, limit));
}

/** File name from the block's first title, without characters file systems refuse. */
export function imageName(title: string | null | undefined, now = new Date()): string {
  const clean = (title || "").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 48).trim();
  if (clean) return `${clean}.png`;
  const p = (n: number) => String(n).padStart(2, "0");
  return `图表-${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}.png`;
}

/** Whether a PNG can go on the clipboard here: needs a secure context and the async clipboard with ClipboardItem. */
export function canCopyImage(): boolean {
  if (typeof window === "undefined" || !window.isSecureContext || typeof ClipboardItem === "undefined" || !navigator.clipboard?.write) return false;
  const supports = (ClipboardItem as unknown as { supports?: (type: string) => boolean }).supports;
  return supports ? supports("image/png") : true;
}

const painted = (style: CSSStyleDeclaration) => !/^(transparent|rgba\(.*,\s*0\))$/.test(style.backgroundColor) || parseFloat(style.borderTopWidth) > 0;

/** Where the actions sit: in the block's corner, or inset when a box (a card, a metric tile) fills that corner so they
 * float inside it rather than on its border. Measured when the block is pointed at, so the corner is on screen. */
export function placeTools(block: HTMLElement) {
  const box = block.getBoundingClientRect();
  // the actions may already be under the point (:hover shows them before this runs): look past them
  const hits = document.elementsFromPoint(box.right - 16, box.top + 16); // inside a rounded corner
  let node: Element | null = hits.find((el) => !el.closest("[data-capture-skip]")) ?? null;
  let boxed = false;
  for (; node && node !== block && block.contains(node); node = node.parentElement) if (painted(getComputedStyle(node))) boxed = true;
  if (boxed) block.dataset.corner = "boxed";
  else delete block.dataset.corner;
}

/** The colour behind the block: the first ancestor that paints one, else the page's. */
function backdrop(el: HTMLElement): string {
  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    if (!/^(transparent|rgba\(.*,\s*0\))$/.test(color)) return color;
  }
  return getComputedStyle(document.documentElement).getPropertyValue("--background").trim() || "#ffffff";
}

/** The block's first title: a heading, a card's or a chart's (whose caption also holds the unit). */
export function titleOf(block: Element): string | undefined {
  const first = block.querySelector(".gu-heading, .gu-card-title, .gu-chart > figcaption");
  return (first?.tagName === "FIGCAPTION" ? first.firstChild : first)?.textContent ?? undefined;
}

/** Left out of the picture: the actions themselves and a chart's pointer readout. */
const skip = (node: Node) => !(node instanceof Element && (node.hasAttribute("data-capture-skip") || node.classList.contains("gu-tip")));

async function render(block: HTMLElement): Promise<Blob> {
  const { domToCanvas } = await import("modern-screenshot");
  // a hover the pointer just left (on its way to the button) commits before the block is copied
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const { width, height } = block.getBoundingClientRect();
  const color = backdrop(block);
  const scale = fitScale(width + 2 * MARGIN, height + 2 * MARGIN, window.devicePixelRatio || 1);
  const drawn = await domToCanvas(block, {
    scale,
    backgroundColor: color,
    filter: skip,
    font: false, // the Portal uses system fonts only: nothing to fetch and embed
    features: { restoreScrollPosition: true },
    timeout: 15000,
  });
  const pad = Math.round(MARGIN * scale);
  const canvas = document.createElement("canvas");
  canvas.width = drawn.width + 2 * pad;
  canvas.height = drawn.height + 2 * pad;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(drawn, pad, pad);
  return new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("toBlob failed"))), "image/png"));
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

type Phase = "idle" | "busy" | "done" | "failed";
type Kind = "copy" | "save";

/** The block's two picture actions, a small floating group shown while the block is pointed at, touched or focused. */
export function ImageTools({ block }: { block: RefObject<HTMLElement | null> }) {
  const [phase, setPhase] = useState<{ kind: Kind; phase: Phase } | null>(null);
  const [copyable] = useState(canCopyImage);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const settle = (kind: Kind, next: Phase) => {
    setPhase({ kind, phase: next });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setPhase(null), next === "failed" ? 2400 : 1400);
  };

  function run(kind: Kind) {
    const el = block.current;
    if (!el || phase?.phase === "busy") return;
    if (timer.current) clearTimeout(timer.current);
    setPhase({ kind, phase: "busy" });
    const picture = render(el);
    const title = titleOf(el);
    // The clipboard item is created inside the click with a pending blob: Safari refuses a write that starts after
    // an await, and the picture takes a moment to draw.
    const done =
      kind === "copy"
        ? navigator.clipboard.write([new ClipboardItem({ "image/png": picture })])
        : picture.then((blob) => save(blob, imageName(title)));
    done.then(() => settle(kind, "done"), () => settle(kind, "failed"));
  }

  const button = (kind: Kind, label: string, icon: ReactNode) => {
    const state = phase?.kind === kind ? phase.phase : "idle";
    const shown = state === "done" ? (kind === "copy" ? "已复制" : "已下载") : state === "failed" ? (kind === "copy" ? "复制失败" : "下载失败") : label;
    return (
      <button type="button" className="icon-btn" data-phase={state} aria-label={shown} title={shown} aria-busy={state === "busy" || undefined} onClick={() => run(kind)}>
        <span className="swap" key={state}>{state === "done" ? <IconCheck size={15} /> : state === "failed" ? <IconX size={15} /> : icon}</span>
      </button>
    );
  };

  return (
    <div className="float-tools genui-tools" data-capture-skip="" data-held={phase ? "" : undefined} role="toolbar" aria-label="图片">
      {copyable ? button("copy", "复制为图片", <IconCopy size={15} />) : null}
      {button("save", "下载图片", <IconDownload size={15} />)}
    </div>
  );
}
