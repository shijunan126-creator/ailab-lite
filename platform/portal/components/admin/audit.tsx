"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { admin, AuditEntry, reason } from "../../lib/admin";
import { formatDate } from "../../lib/format";
import Select from "../select";

// The API's action names (exact match); anything newer shows its code until it is named here.
const ACTION_LABEL: Record<string, string> = {
  "auth.login": "登录",
  "auth.logout": "退出登录",
  "auth.register": "注册",
  "users.create": "新建用户",
  "users.update": "修改用户",
  "users.password": "修改密码",
  "users.list": "查看用户",
  "departments.create": "新建部门",
  "departments.update": "修改部门",
  "departments.delete": "删除部门",
  "departments.list": "查看部门",
  "quota-policies.create": "新建额度",
  "quota-policies.update": "修改额度",
  "quota-policies.delete": "删除额度",
  "quota-policies.list": "查看额度",
  "usage.list": "查看用量",
  "audit.list": "查看审计",
};

const GROUPS: [string, string[]][] = [
  ["账号", ["auth.login", "auth.logout", "auth.register"]],
  ["用户", ["users.create", "users.update", "users.password"]],
  ["部门", ["departments.create", "departments.update", "departments.delete"]],
  ["额度", ["quota-policies.create", "quota-policies.update", "quota-policies.delete"]],
  ["查看", ["users.list", "departments.list", "quota-policies.list", "usage.list", "audit.list"]],
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
        <Select
          aria-label="按操作筛选"
          value={action}
          onChange={setAction}
          options={[{ value: "", label: "全部操作" }, ...GROUPS.flatMap(([group, actions]) => actions.map((k) => ({ value: k, label: ACTION_LABEL[k], group })))]}
        />
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
                <span className="meta">{[e.target_type, e.target_id].filter(Boolean).join(" · ")}</span>
              </span>
              <span className={`status ${e.outcome === "success" ? "succeeded" : "failed"}`}>{e.outcome === "success" ? "成功" : "失败"}</span>
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
