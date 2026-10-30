/** Collects stream events and hands them over at most once per frame (every 250ms while the page is hidden,
 * where animation frames stop). Many deltas between two paints become one state update. */
export function createBatcher<T>(flush: (batch: T[]) => void) {
  let queue: T[] = [];
  let frame: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const run = () => {
    frame = null;
    timer = null;
    if (!queue.length) return;
    const batch = queue;
    queue = [];
    flush(batch);
  };
  const cancel = () => {
    if (frame !== null) cancelAnimationFrame(frame);
    if (timer !== null) clearTimeout(timer);
    frame = timer = null;
  };
  return {
    push(item: T) {
      queue.push(item);
      if (frame !== null || timer !== null) return;
      if (typeof document !== "undefined" && document.hidden) timer = setTimeout(run, 250);
      else frame = requestAnimationFrame(run);
    },
    /** Deliver whatever is waiting now (the stream ended, or something must see the latest state). */
    flush() {
      cancel();
      run();
    },
    cancel() {
      cancel();
      queue = [];
    },
  };
}
