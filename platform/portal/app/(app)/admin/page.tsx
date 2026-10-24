"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import SkillsSection from "../../../components/admin/skills";
import MarketReviewSection from "../../../components/admin/market";
import AuditSection from "../../../components/admin/audit";
import BrandSection from "../../../components/admin/brand";
import ContextSection from "../../../components/admin/context";
import DepartmentsSection from "../../../components/admin/departments";
import PoliciesSection from "../../../components/admin/policies";
import UsageSection from "../../../components/admin/usage";
import UsersSection from "../../../components/admin/users";
import { api, Me } from "../../../lib/api";

type Tab = "users" | "departments" | "policies" | "usage" | "audit" | "skills" | "market" | "brand" | "context";
const TABS: [Tab, string][] = [
  ["users", "用户"],
  ["departments", "部门"],
  ["policies", "额度"],
  ["usage", "用量"],
  ["audit", "审计"],
  ["skills", "技能"],
  ["market", "市场"],
  ["brand", "品牌"],
  ["context", "上下文"],
];

/** Which tabs a role sees; the API enforces the same, this only avoids offering what would be refused. */
function visible(me: Me): Tab[] {
  const roles = new Set(me.roles || []);
  if (roles.has("platform_admin")) return TABS.map(([t]) => t);
  const tabs: Tab[] = [];
  if (roles.has("dept_admin")) tabs.push("users", "usage");
  if (roles.has("auditor")) tabs.push("usage", "audit");
  return [...new Set(tabs)];
}

function Admin() {
  const router = useRouter();
  const params = useSearchParams();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api<Me>("/v1/auth/me")
      .then((m) => (m.can_admin ? setMe(m) : router.replace("/")))
      .catch(() => router.replace("/"));
  }, [router]);

  if (!me) return null;
  const tabs = visible(me);
  const tab = (tabs.find((t) => t === params.get("t")) || tabs[0]) as Tab;
  const isPlatform = !!me.roles?.includes("platform_admin");

  return (
    <main className="page page-admin">
      <div className="library-head">
        <h1>管理</h1>
      </div>
      <div className="library-bar">
        <div className="tabs" role="tablist">
          {TABS.filter(([t]) => tabs.includes(t)).map(([key, label]) => (
            <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => router.push(`/admin?t=${key}`)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === "users" ? <UsersSection me={me} isPlatform={isPlatform} /> : null}
      {tab === "departments" ? <DepartmentsSection /> : null}
      {tab === "policies" ? <PoliciesSection /> : null}
      {tab === "usage" ? <UsageSection /> : null}
      {tab === "audit" ? <AuditSection /> : null}
      {tab === "skills" ? <SkillsSection /> : null}
      {tab === "market" ? <MarketReviewSection /> : null}
      {tab === "brand" ? <BrandSection /> : null}
      {tab === "context" ? <ContextSection /> : null}
    </main>
  );
}

export default function AdminPage() {
  return (
    <Suspense fallback={null}>
      <Admin />
    </Suspense>
  );
}
