"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { reason } from "../../lib/admin";
import { formatBytes, timeAgo } from "../../lib/format";
import { IconSearch } from "../icons";

type Uploader = { id: string; username: string | null; display_name: string };
type Provenance = {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  sha256: string;
  status: string;
  version: number;
  lineage_id: string;
  project_id: string | null;
  created_at: string;
  deleted_at: string | null;
  purged: boolean;
  uploader: Uploader | null;
};

export default function FilesSection() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Provenance[] | null>(null);
  const [detail, setDetail] = useState<Provenance[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async (query: string) => {
    try {
      const res = await api<{ items: Provenance[] }>(`/v1/admin/files?q=${encodeURIComponent(query)}`);
      setItems(res.items);
      setError("");
    } catch (e) {
      setError(reason(e));
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load(q.trim()), 200);
    return () => clearTimeout(timer);
  }, [q, load]);

  async function open(id: string) {
    try {
      const res = await api<{ versions: Provenance[] }>(`/v1/admin/files/${id}`);
      setDetail(res.versions);
    } catch (e) {
      setError(reason(e));
    }
  }

  return (
    <section className="admin-section">
      <h2>文件溯源</h2>
      <p className="hint">只显示文件元数据，不读取文件正文。可按文件名、文件 ID 或 SHA-256 搜索。</p>
      <div className="admin-toolbar">
        <label className="search">
          <IconSearch size={15} />
          <input
            type="text"
            placeholder="文件名 / 文件 ID / SHA-256"
            aria-label="搜索文件"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {items === null ? <div className="empty">加载中…</div> : null}
      {items?.length === 0 ? <div className="empty">没有找到文件</div> : null}
      <div className="admin-list">
        {(items || []).map((file) => (
          <button key={file.id} className="admin-row" onClick={() => open(file.id)}>
            <span className="grow">
              <span className="name">{file.filename}</span>
              <span className="meta">
                {file.uploader?.display_name || file.uploader?.username || "未知账号"}
                {" · "}
                {timeAgo(file.created_at)}
                {file.purged ? " · 已清除" : file.deleted_at ? " · 已删除" : ""}
              </span>
            </span>
            <span className="col">{formatBytes(file.size)}</span>
            <span className="col">v{file.version}</span>
          </button>
        ))}
      </div>
      {detail ? (
        <div className="card" style={{ marginTop: 16, padding: 16 }}>
          <div className="row" style={{ alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>版本记录</h3>
            <span className="spacer" />
            <button className="link" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="admin-list">
            {detail.map((file) => (
              <div key={file.id} className="admin-row">
                <span className="grow">
                  <span className="name">{file.filename}</span>
                  <span className="meta">
                    v{file.version} · {file.uploader?.display_name || file.uploader?.username || "未知账号"} · {timeAgo(file.created_at)}
                  </span>
                </span>
                <span className="col">{formatBytes(file.size)}</span>
                <span className="col">{file.status}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
