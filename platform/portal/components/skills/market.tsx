"use client";

import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { skillIcon } from "../chat/composer";
import { IconCheck, IconPlus } from "../icons";

/** An entry of the official catalog (GET /v1/market/skills), with what this user already has. */
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

/** 技能广场: official skills to add. Adding copies one into the personal skills, where it is scanned like an upload. */
export default function Market({ onAdded }: { onAdded: (fileId: string, title: string) => void }) {
  const [items, setItems] = useState<MarketSkill[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<MarketSkill[]>("/v1/market/skills", { signal: controller.signal })
      .then(setItems)
      .catch((e) => !controller.signal.aborted && setError(errorMessage(e)));
    return () => controller.abort();
  }, []);

  const groups = useMemo(() => {
    const byCategory = new Map<string, MarketSkill[]>();
    for (const item of items || []) byCategory.set(item.category || "其他", [...(byCategory.get(item.category || "其他") || []), item]);
    return [...byCategory];
  }, [items]);

  async function add(item: MarketSkill) {
    setBusy(item.slug);
    setError("");
    try {
      const saved = await api<{ file_id: string }>(`/v1/market/skills/${encodeURIComponent(item.slug)}/install`, { method: "POST" });
      setItems((list) => list?.map((x) => (x.slug === item.slug ? { ...x, installed: true, installed_file_id: saved.file_id } : x)) || list);
      onAdded(saved.file_id, item.title);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {error ? <div className="alert error" style={{ marginBottom: 12 }}>{error}</div> : null}
      {items === null && !error ? <div className="mlist"><div className="mlist-empty">加载中…</div></div> : null}
      {items?.length === 0 ? <div className="mlist"><div className="mlist-empty">广场里还没有技能</div></div> : null}
      {groups.map(([category, list]) => (
        <section key={category} className="msection">
          <div className="msection-head"><h2>{category}</h2></div>
          <div className="market-grid">
            {list.map((item) => {
              const Icon = skillIcon(item.name);
              const added = item.builtin || item.installed;
              return (
                <article key={item.slug} className="market-card">
                  <div className="market-card-head">
                    <span className="mrow-icon"><Icon size={17} /></span>
                    <span className="market-title" title={item.title}>{item.title}</span>
                  </div>
                  <p className="market-desc" title={item.description}>{item.description}</p>
                  {item.installed_error ? <p className="market-error">{item.installed_error}</p> : null}
                  <button type="button" className={`market-add sm ${added ? "secondary" : ""}`} disabled={added || busy === item.slug} onClick={() => add(item)}>
                    {added ? <IconCheck size={14} /> : <IconPlus size={14} />}
                    {item.builtin ? "内置" : item.installed ? "已添加" : busy === item.slug ? "添加中…" : "添加"}
                  </button>
                </article>
              );
            })}
          </div>
        </section>
      ))}
    </>
  );
}
