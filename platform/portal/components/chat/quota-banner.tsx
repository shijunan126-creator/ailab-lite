"use client";

import { useEffect, useState } from "react";
import { api, Usage, UsageAlert } from "../../lib/api";

const WINDOW = { daily_tokens: "今日", monthly_tokens: "本月" } as const;

function resetsAt(value: string): string {
  const d = new Date(value);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const time = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === today.toDateString()) return `今天 ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `明天 ${time}`;
  return d.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
}

/** Shown only near or over the quota: the most pressing window, and when it resets. */
export default function QuotaBanner({ refresh }: { refresh: unknown }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    let stale = false;
    api<Usage>("/v1/usage")
      .then((u) => !stale && setUsage(u))
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [refresh]);
  const alerts = usage?.alerts || [];
  const alert: UsageAlert | undefined = alerts.find((a) => a.code === "exceeded") || alerts[0];
  if (!alert || !usage) return null;
  const window = usage[alert.window];
  const over = alert.code === "exceeded";
  return (
    <div className={`quota-banner ${over ? "over" : ""}`} role="status">
      {over ? `${WINDOW[alert.window]}额度已用完` : `${WINDOW[alert.window]}额度已用 ${Math.min(99, Math.round(window.percent))}%`}
      <span>{resetsAt(alert.resets_at)} 恢复</span>
    </div>
  );
}
