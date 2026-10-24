"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { reason } from "../../lib/admin";

type Pending = {
  listing_id: string;
  slug: string;
  title: string;
  owner_id: string;
  version: string | null;
  created_at: string;
};

type Detail = {
  listing: { id: string; slug: string; title: string; description: string; category: string; license: string; owner_id: string; status: string };
  version: { id: string; version: string; status: string; changelog: string; reason: string } | null;
  files: string[];
  skill_md: string;
};

export default function MarketReviewSection() {
  const [items, setItems] = useState<Pending[] | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function load() {
    setError("");
    api<Pending[]>("/v1/market/admin/submissions?status=pending")
      .then(setItems)
      .catch((e) => setError(reason(e)));
  }
  useEffect(load, []);

  async function open(id: string) {
    setDetail(null);
    try {
      setDetail(await api<Detail>(`/v1/market/admin/submissions/${id}`));
    } catch (e) {
      setError(reason(e));
    }
  }

  async function review(decision: "approved" | "rejected") {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      await api(`/v1/market/admin/submissions/${detail.listing.id}/review`, {
        method: "POST",
        body: JSON.stringify({ decision, reason: note }),
      });
      setDetail(null);
      setNote("");
      load();
    } catch (e) {
      setError(reason(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-section">
      <h2>技能市场上架审核</h2>
      {error ? <div className="alert error">{error}</div> : null}
      {items === null ? <div className="empty">加载中…</div> : null}
      {items !== null && !items.length ? <div className="empty">暂无待审提交</div> : null}
      <div className="market-review-list">
        {(items || []).map((item) => (
          <button key={item.listing_id} className="admin-row" onClick={() => open(item.listing_id)}>
            <span className="grow">
              <strong>{item.title}</strong>
              <span className="hint"> · {item.slug} · v{item.version || "?"}</span>
            </span>
            <span className="chip">待审</span>
          </button>
        ))}
      </div>
      {detail ? (
        <div className="card" style={{ marginTop: 20, padding: 16 }}>
          <div className="row" style={{ alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>{detail.listing.title}</h3>
            <span className="spacer" />
            <span className="chip">{detail.listing.slug}</span>
            <span className="chip">{detail.listing.license}</span>
            <button className="link" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <p>{detail.listing.description}</p>
          <p className="hint">文件：{detail.files.join("、") || "无"}</p>
          <pre className="prose" style={{ maxHeight: 320, overflow: "auto", background: "var(--surface-2)", padding: 12, borderRadius: 8 }}>
            {detail.skill_md || "无法读取 SKILL.md"}
          </pre>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="拒绝原因（通过时可留空）"
            rows={3}
            style={{ marginTop: 12 }}
          />
          <div className="modal-foot">
            <span className="spacer" />
            <button className="secondary" disabled={busy} onClick={() => review("rejected")}>拒绝</button>
            <button disabled={busy} onClick={() => review("approved")}>通过并上架</button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
