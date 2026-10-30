import { useCallback, useLayoutEffect, useRef } from "react";

/** A callback whose identity never changes but always calls the latest render's function, so memoized children
 * that receive it are not re-rendered by every parent render. Not for use during render. */
export function useStable<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn);
  useLayoutEffect(() => {
    ref.current = fn;
  });
  return useCallback((...args: A) => ref.current(...args), []);
}
