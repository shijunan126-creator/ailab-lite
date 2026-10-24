"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";

type Submission = {
  listing_id: string;
  slug: string;
  title: string;
  status: string;
  version: string | null;
  version_status: string | null;
  reason: string;
  created_at: string;
};

const LABEL: Record<string, string> = {
  pending: "审核中",
  published: "已上架",
  rejected: "未通过",
  withdrawn: "已撤回",
  delisted: "已下架",
};

export default function MarketSubmissions({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<Submission[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<Submission[]>("/v1/market/submissions/mine")
      .then(setItems)
      .catch((e) => setError(String(e?.message || e)));
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function withdraw(id: string) {
    if (!confirm("撤回这次上架申请？")) return;
    try {
      await api(`/v1/market/submissions/${id}/withdraw`, { method: "POST" });
      load();
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  if (!items.length && !error) return null;
  return (
    <section className="card skill-list" style={{ marginTop: 16 }}>
      <div className="section-title" style={{ padding: "12px 18px 6px" }}>我的上架</div>
      {error ? <div className="alert error" style={{ margin: 12 }}>{error}</div> : null}
      {items.map((item) => (
        <div key={item.listing_id} className="skill-row">
          <div className="grow">
            <div className="skill-title">
              {item.title}
              <span className="chip mono">{item.slug}</span>
              <span className="chip">{LABEL[item.status] || item.status}</span>
            </div>
            {item.reason ? <div className="hint error">{item.reason}</div> : null}
          </div>
          {item.status === "pending" || item.status === "rejected" ? (
            <button className="link" onClick={() => withdraw(item.listing_id)}>
              撤回
            </button>
          ) : null}
        </div>
      ))}
    </section>
  );
}
