"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { IconDownload, IconSparkles } from "../icons";

export type MarketSkill = {
  slug: string;
  name: string;
  title: string;
  description: string;
  category: string;
  version: string;
  license: string;
  author: string;
  requires: string[];
  builtin: boolean;
  installed: boolean;
  installed_file_id: string | null;
  installed_error: string | null;
};

export default function MarketPanel({
  onInstalled,
}: {
  onInstalled: (fileId: string, title: string) => void;
}) {
  const [items, setItems] = useState<MarketSkill[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<MarketSkill[]>("/v1/market/skills")
      .then((list) => alive && setItems(list))
      .catch((e) => alive && setError(String(e?.message || e)));
    return () => {
      alive = false;
    };
  }, []);

  async function install(item: MarketSkill) {
    setBusy(item.slug);
    setError("");
    try {
      const saved = await api<{ file_id: string; title: string; status: string }>(
        `/v1/market/skills/${item.slug}/install`,
        { method: "POST" }
      );
      setItems((list) => list?.map((x) => (x.slug === item.slug ? { ...x, installed: true, installed_file_id: saved.file_id } : x)) || list);
      onInstalled(saved.file_id, item.title);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  const groups = (items || []).reduce<Record<string, MarketSkill[]>>((out, item) => {
    (out[item.category] ||= []).push(item);
    return out;
  }, {});

  return (
    <section className="card skill-list">
      {error ? <div className="alert error" style={{ margin: 12 }}>{error}</div> : null}
      {items === null ? <div className="empty">加载中…</div> : null}
      {items !== null && !items.length ? <div className="empty">市场里还没有技能</div> : null}
      {Object.entries(groups).map(([category, list]) => (
        <div key={category}>
          <div className="section-title" style={{ margin: "18px 16px 6px", fontSize: 15 }}>{category}</div>
          {list.map((item) => (
            <div key={item.slug} className="skill-row">
              <span className="skill-icon">
                <IconSparkles size={20} />
              </span>
              <div className="grow">
                <div className="skill-title">
                  {item.title}
                  <span className="chip mono">{item.name}</span>
                  {item.builtin ? <span className="chip">内置</span> : <span className="chip">可安装</span>}
                </div>
                <div className="skill-desc">{item.description}</div>
                <div className="skill-file hint">
                  {item.author} · v{item.version} · {item.license}
                  {item.requires.length ? ` · 依赖 ${item.requires.join("、")}` : ""}
                  {item.installed_error ? <span className="error">　无法使用：{item.installed_error}</span> : null}
                </div>
              </div>
              <button
                className={item.builtin || item.installed ? "secondary sm" : "primary sm"}
                disabled={item.builtin || item.installed || busy === item.slug}
                onClick={() => install(item)}
              >
                <IconDownload size={14} />{" "}
                {item.builtin ? "已内置" : item.installed ? "已添加" : busy === item.slug ? "安装中…" : "添加到我的技能"}
              </button>
            </div>
          ))}
        </div>
      ))}
      <div className="hint" style={{ padding: 16 }}>
        安装会把技能复制到你的个人技能，之后可以编辑、删除；更新需要重新安装。
      </div>
    </section>
  );
}
