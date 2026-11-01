"use client";

import { useEffect, useMemo, useState } from "react";
import { api, errorMessage, Page, PlatformSkill } from "../../lib/api";
import { skillIcon } from "../chat/composer";
import { IconCheck, IconPlus } from "../icons";

/** 技能广场: the platform skills admins publish. Adding one follows the platform's version until the user changes it. */
export default function Market({ onAdded }: { onAdded: () => void }) {
  const [items, setItems] = useState<PlatformSkill[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<Page<PlatformSkill>>("/v1/platform-skills", { signal: controller.signal })
      .then((page) => {
        setItems(page.items);
        setCursor(page.next_cursor);
      })
      .catch((e) => !controller.signal.aborted && setError(errorMessage(e)));
    return () => controller.abort();
  }, []);

  async function more() {
    if (!cursor || busy) return;
    setBusy("more");
    try {
      const page = await api<Page<PlatformSkill>>(`/v1/platform-skills?after=${encodeURIComponent(cursor)}`);
      setItems((list) => [...(list || []), ...page.items.filter((x) => !list?.some((y) => y.id === x.id))]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const groups = useMemo(() => {
    const byCategory = new Map<string, PlatformSkill[]>();
    for (const item of items || []) {
      const key = item.category || "其他";
      byCategory.set(key, [...(byCategory.get(key) || []), item]);
    }
    return [...byCategory];
  }, [items]);

  async function add(item: PlatformSkill) {
    setBusy(item.id);
    setError("");
    try {
      const saved = await api<PlatformSkill>(`/v1/platform-skills/${encodeURIComponent(item.id)}/add`, { method: "POST" });
      setItems((list) => list?.map((x) => (x.id === item.id ? { ...x, ...saved, added: true } : x)) || list);
      onAdded();
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
              return (
                <article key={item.id} className="market-card">
                  <div className="market-card-head">
                    <span className="mrow-icon"><Icon size={17} /></span>
                    <span className="market-title" title={item.title}>{item.title}</span>
                  </div>
                  <p className="market-desc" title={item.description}>{item.description}</p>
                  <div className="market-foot">
                    {item.source_url ? (
                      <a className="market-source" href={item.source_url} target="_blank" rel="noreferrer noopener" title={item.license ? `${item.license} 许可` : undefined}>
                        来源
                      </a>
                    ) : <span />}
                    <button type="button" className={`market-add sm ${item.added ? "secondary" : ""}`} disabled={item.added || busy === item.id} onClick={() => add(item)}>
                      {item.added ? <IconCheck size={14} /> : <IconPlus size={14} />}
                      {item.added ? "已添加" : busy === item.id ? "添加中…" : "添加"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ))}
      {cursor ? (
        <button type="button" className="secondary sm market-more" disabled={busy === "more"} onClick={more}>
          {busy === "more" ? "加载中…" : "更多"}
        </button>
      ) : null}
    </>
  );
}
