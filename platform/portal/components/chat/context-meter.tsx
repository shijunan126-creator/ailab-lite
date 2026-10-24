"use client";

import { formatTokens } from "../../lib/format";
export { estimateTokens } from "../../lib/tokens";

/** A small ring by the send button: how full the conversation's context is, the message being typed included.
 * Fixed size, in a reserved slot: it never moves the layout; the numbers are in its tooltip. */
export default function ContextMeter({ used, limit, compactAt, draft }: { used: number | null; limit: number; compactAt: number | null; draft: number }) {
  const total = (used || 0) + draft;
  const share = limit > 0 ? Math.min(1, total / limit) : 0;
  const level = share >= 0.95 ? "full" : share >= 0.8 || (compactAt !== null && total >= compactAt) ? "warn" : "";
  const r = 7;
  const circumference = 2 * Math.PI * r;
  const lines = [
    draft ? `本条约 ${draft.toLocaleString()} tokens` : null,
    `上下文 ${formatTokens(total)} / ${formatTokens(limit)}（${Math.round(share * 100)}%）`,
    compactAt !== null ? `超过 ${formatTokens(compactAt)} 时自动压缩较早的对话` : null,
  ].filter(Boolean);
  return (
    <span className={`ctx-meter ${level}`} tabIndex={0} aria-label={lines.join("，")}>
      <span className="ctx-draft" aria-hidden="true">{draft ? `≈${formatTokens(draft)}` : ""}</span>
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <circle cx="9" cy="9" r={r} className="ctx-track" />
        <circle cx="9" cy="9" r={r} className="ctx-fill" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - share)} />
      </svg>
      <span className="ctx-tip" role="tooltip">
        {lines.map((line) => (
          <span key={line}>{line}</span>
        ))}
      </span>
    </span>
  );
}
