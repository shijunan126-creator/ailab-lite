/** A CSS time ("0.28s", ".28s", "280ms") in milliseconds; 0 when it is not one. */
export function durationMs(value: string): number {
  const n = parseFloat(value) || 0;
  return value.trim().endsWith("ms") ? n : n * 1000;
}
