/** Select a precompressed representation, respecting explicit exclusions and identity fallback. */
export function assetEncoding(accept: string | null): "br" | "gzip" | null {
  const weights = new Map((accept ?? "").toLowerCase().split(",").map((entry) => {
    const [name, ...params] = entry.trim().split(";");
    const quality = params.find((p) => p.trim().startsWith("q="))?.trim().slice(2);
    const q = quality === undefined ? 1 : Number(quality);
    return [name, Number.isFinite(q) && q >= 0 && q <= 1 ? q : 0] as const;
  }));
  const br = weights.get("br") ?? weights.get("*") ?? 0;
  const gzip = weights.get("gzip") ?? weights.get("*") ?? 0;
  return br > 0 && br >= gzip ? "br" : gzip > 0 ? "gzip" : null;
}
