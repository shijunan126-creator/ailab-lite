"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { admin, AuditEntry, reason } from "../../lib/admin";
import { formatDate } from "../../lib/format";

// Known actions read as words; anything newer shows its code until it is named here.
const ACTION_LABEL: Record<string, string> = {
  "auth.login": "登录",
  "auth.login_failed": "登录失败",
  "auth.logout": "退出登录",
  "auth.register": "注册",
  "user.create": "新建用户",
  "user.update": "修改用户",
  "user.disable": "停用用户",
  "user.enable": "启用用户",
  "user.password_reset": "重置密码",
  "user.roles": "修改角色",
  "department.create": "新建部门",
  "department.update": "修改部门",
  "department.delete": "删除部门",
  "quota_policy.create": "新建额度",
  "quota_policy.update": "修改额度",
  "quota_policy.delete": "删除额度",
};

const FILTERS: [string, string][] = [
  ["", "全部操作"],
  ["auth", "登录与注册"],
  ["user", "用户"],
  ["department", "部门"],
  ["quota_policy", "额度"],
];

export default function AuditSection() {
  const [action, setAction] = useState("");
  const [items, setItems] = useState<AuditEntry[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const seq = useRef(0);

  const load = useCallback(
    async (after: string | null) => {
      const mine = ++seq.current;
      try {
        const page = await admin.audit({ action, cursor: after });
        if (mine !== seq.current) return;
        setItems((prev) => (after && prev ? [...prev, ...page.items] : page.items));
        setCursor(page.next_cursor);
        setError("");
      } catch (err) {
        if (mine === seq.current) setError(reason(err));
      }
    },
    [action]
  );

  useEffect(() => {
    load(null);
  }, [load]);

  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="按操作筛选">
          {FILTERS.map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {items?.length === 0 ? <div className="empty">没有记录</div> : null}
      <div className="admin-list audit-list">
        {(items || []).map((e) => (
          <div key={e.id} className={`audit-item ${open === e.id ? "open" : ""}`}>
            <button className="admin-row plain" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
              <span className="col when">{formatDate(e.at)}</span>
              <span className="col who">{e.actor?.name || e.actor_service || "系统"}</span>
              <span className="grow">
                <span className="name">{ACTION_LABEL[e.action] || e.action}</span>
                <span className="meta">
                  {e.target_type} · {e.target_id}
                </span>
              </span>
              <span className={`status ${e.outcome === "success" || e.outcome === "ok" ? "succeeded" : "failed"}`}>{e.outcome === "success" || e.outcome === "ok" ? "成功" : e.outcome === "denied" ? "拒绝" : "失败"}</span>
            </button>
            {open === e.id ? (
              <pre className="audit-detail">{JSON.stringify({ request_id: e.request_id, ...(e.detail || {}) }, null, 2)}</pre>
            ) : null}
          </div>
        ))}
      </div>
      {cursor ? (
        <div className="load-more">
          <button className="link" onClick={() => load(cursor)}>
            加载更多
          </button>
        </div>
      ) : null}
    </section>
  );
}
