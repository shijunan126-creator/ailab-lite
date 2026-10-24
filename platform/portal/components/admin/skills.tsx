"use client";

import { useEffect, useState } from "react";
import { api, SkillPolicy, SkillReview } from "../../lib/api";
import { admin, Department, reason } from "../../lib/admin";
import Select from "../select";

type Catalog = { name: string; title: string; builtin: boolean };
type ReviewPage = { items: SkillReview[]; next_cursor: string | null };

const PERSONAL = "*personal*";
const STATE_LABEL: Record<SkillPolicy["state"], string> = { allowed: "允许", review: "需审核", disabled: "禁用" };

function stateOf(rules: SkillPolicy[], scope: string, name: string): SkillPolicy["state"] | undefined {
  return rules.find((p) => p.scope === scope && p.name === name)?.state;
}

async function allPolicies(): Promise<SkillPolicy[]> {
  const rules: SkillPolicy[] = [];
  let after: string | null = "";
  do {
    const page: { items: SkillPolicy[]; next_cursor: string | null } = await api(`/v1/skills/admin/policies?after=${encodeURIComponent(after || "")}`);
    rules.push(...page.items);
    after = page.next_cursor;
  } while (after);
  return rules;
}

export default function SkillsSection() {
  const [scope, setScope] = useState("global");
  const [departments, setDepartments] = useState<Department[]>([]);
  const [catalog, setCatalog] = useState<Catalog[]>([]);
  const [policies, setPolicies] = useState<SkillPolicy[]>([]);
  const [reviews, setReviews] = useState<SkillReview[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Catalog[]>("/v1/skills"),
      allPolicies(),
      admin.departments(),
      api<ReviewPage>("/v1/skills/admin/reviews"),
    ])
      .then(([skills, rules, depts, page]) => {
        if (!active) return;
        setCatalog(skills.filter((s) => s.builtin));
        setPolicies(rules);
        setDepartments(depts);
        setReviews(page.items);
        setCursor(page.next_cursor);
      })
      .catch((err) => {
        if (active) setError(reason(err));
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(name: string, state: SkillPolicy["state"]) {
    setBusy(true);
    setError("");
    try {
      const rule = await api<SkillPolicy>("/v1/skills/admin/policies", {
        method: "PUT",
        body: JSON.stringify({ scope, name, state }),
      });
      setPolicies((old) => [...old.filter((p) => p.scope !== rule.scope || p.name !== rule.name), rule]);
    } catch (err) {
      setError(reason(err));
    } finally {
      setBusy(false);
    }
  }

  async function review(fileId: string, state: "allowed" | "disabled") {
    setBusy(true);
    setError("");
    try {
      await api(`/v1/skills/admin/reviews/${fileId}`, { method: "PUT", body: JSON.stringify({ state }) });
      setReviews((old) => old.filter((s) => s.file_id !== fileId));
    } catch (err) {
      setError(reason(err));
    } finally {
      setBusy(false);
    }
  }

  async function more() {
    setBusy(true);
    try {
      const page = await api<ReviewPage>(`/v1/skills/admin/reviews?after=${encodeURIComponent(cursor || "")}`);
      setReviews((old) => [...old, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setError(reason(err));
    } finally {
      setBusy(false);
    }
  }

  const cards = [...catalog, { name: PERSONAL, title: "个人技能", builtin: false }];
  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <Select
          aria-label="技能策略范围"
          value={scope}
          disabled={busy}
          onChange={setScope}
          options={[{ value: "global", label: "全平台" }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
        />
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      <div className="policy-grid">
        {cards.map((skill) => {
          const value = stateOf(policies, scope, skill.name) ?? "allowed";
          const global = stateOf(policies, "global", skill.name);
          const options: SkillPolicy["state"][] = skill.name === PERSONAL ? ["allowed", "review", "disabled"] : ["allowed", "disabled"];
          return (
            <div key={skill.name} className="policy-card">
              <div className="policy-foot">
                <span>{skill.title}</span>
                <Select
                  aria-label={skill.title}
                  disabled={busy}
                  value={value}
                  onChange={(v) => save(skill.name, v as SkillPolicy["state"])}
                  options={options.map((state) => ({ value: state, label: STATE_LABEL[state] }))}
                />
                {scope !== "global" && global && global !== "allowed" ? <span className="chip">全平台：{STATE_LABEL[global]}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
      <h2>待审核</h2>
      {reviews.length === 0 ? <div className="empty">暂无待审核技能</div> : null}
      {reviews.map((s) => (
        <div key={s.file_id} className="admin-toolbar">
          <span>{s.name}</span>
          <span className="spacer" />
          <button className="secondary" disabled={busy} onClick={() => review(s.file_id, "disabled")}>
            拒绝
          </button>
          <button disabled={busy} onClick={() => review(s.file_id, "allowed")}>
            通过
          </button>
        </div>
      ))}
      {cursor ? (
        <button className="secondary" disabled={busy} onClick={more}>
          加载更多
        </button>
      ) : null}
    </section>
  );
}
