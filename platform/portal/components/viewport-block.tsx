"use client";

import { CSSProperties, ReactNode, useLayoutEffect, useRef, useState } from "react";

type Watch = (visible: boolean) => void;
const observers = new Map<Element | null, { observer: IntersectionObserver; targets: Map<Element, Watch> }>();

// Share one observer per scroll container, rather than one observer/listener per message or text chunk.
function watch(node: HTMLElement, callback: Watch) {
  const root = node.parentElement?.closest(".work-timeline, .messages, .viewer-body") || null;
  let shared = observers.get(root);
  if (!shared) {
    const targets = new Map<Element, Watch>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) targets.get(entry.target)?.(entry.isIntersecting);
    }, { root, rootMargin: "600px 0px" });
    shared = { observer, targets };
    observers.set(root, shared);
  }
  shared.targets.set(node, callback);
  shared.observer.observe(node);
  return () => {
    shared!.observer.unobserve(node);
    shared!.targets.delete(node);
    if (!shared!.targets.size) { shared!.observer.disconnect(); observers.delete(root); }
  };
}

/** Keep measured space while offscreen. Callers keep their state outside the deferred subtree. */
export function useViewport<T extends HTMLElement = HTMLDivElement>(pinned = false, estimate = 240) {
  const ref = useRef<T | null>(null);
  const height = useRef(estimate);
  const [near, setNear] = useState(false);
  const [focused, setFocused] = useState(false);
  const visible = pinned || near || focused;
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const unwatch = watch(node, setNear);
    const focus = () => setFocused(true);
    const blur = () => setFocused(node.contains(document.activeElement));
    node.addEventListener("focusin", focus);
    node.addEventListener("focusout", blur);
    return () => { unwatch(); node.removeEventListener("focusin", focus); node.removeEventListener("focusout", blur); };
  }, []);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node || !visible) return;
    const measure = () => { height.current = node.getBoundingClientRect().height || estimate; };
    measure();
    const resize = new ResizeObserver(measure);
    resize.observe(node);
    return () => resize.disconnect();
  }, [visible, estimate]);
  const style: CSSProperties | undefined = visible ? undefined : { height: height.current, overflow: "hidden" };
  return { ref, visible, style };
}

export default function ViewportBlock({ children, estimate = 240 }: { children: ReactNode; estimate?: number }) {
  const viewport = useViewport(false, estimate);
  return <div className="viewport-block" ref={viewport.ref} style={viewport.style} data-mounted={viewport.visible}>
    {viewport.visible ? children : null}
  </div>;
}
