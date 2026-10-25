"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { admin, AdminUser, Department, QuotaPolicy, reason, UserUsage } from "../../lib/admin";
import { Me } from "../../lib/api";
import { formatTokens, timeAgo } from "../../lib/format";
import { IconPlus, IconSearch, IconX } from "../icons";
import Modal from "../library/modal";
import Select from "../select";

function AdminToggle({ on, onChange, disabled }: { on: boolean; onChange: (on: boolean) => void; disabled?: boolean }) {
  return (
    <div className="field-row">
      <span>{on ? "管理者" : "普通用户"}</span>
      <button
        type="button"
        className={`switch ${on ? "on" : ""}`}
        role="switch"
        aria-checked={on}
        aria-label="管理者权限"
        disabled={disabled}
        onClick={() => onChange(!on)}
      >
        <span className="knob" />
      </button>
    </div>
  );
}

function UserDialog({
  user,
  departments,
  policies,
  isPlatform,
  onSaved,
  onClose,
}: {
  user: AdminUser;
  departments: Department[];
  policies: QuotaPolicy[];
  isPlatform: boolean;
  onSaved: (user: AdminUser) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(user.display_name);
  const [dept, setDept] = useState(user.dept?.id || "");
  const [isAdmin, setIsAdmin] = useState(user.roles.some((r) => r !== "user"));
  const [policy, setPolicy] = useState(user.quota_policy?.id || "");
  const [disabled, setDisabled] = useState(user.disabled);
  const [password, setPassword] = useState("");
  const [usage, setUsage] = useState<UserUsage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    admin.userUsage(user.id).then(setUsage).catch(() => setUsage(null));
  }, [user.id]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const change: Parameters<typeof admin.patchUser>[1] = {};
      if (name.trim() !== user.display_name) change.display_name = name.trim();
      if ((dept || null) !== (user.dept?.id || null)) change.dept_id = dept || null;
      if ((policy || null) !== (user.quota_policy?.id || null)) change.quota_policy_id = policy || null;
      if (disabled !== user.disabled) change.disabled = disabled;
      const before = user.roles.some((r) => r !== "user");
      if (isAdmin !== before) change.roles = isAdmin ? ["user", "platform_admin"] : ["user"];
      const saved = Object.keys(change).length ? await admin.patchUser(user.id, change) : user;
      if (password) await admin.setPassword(user.id, password);
      onSaved(saved);
    } catch (err) {
      setError(reason(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={user.display_name || user.username}
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="user-form" disabled={busy || !name.trim()}>
            保存
          </button>
        </>
      }
    >
      <form id="user-form" className="admin-form" onSubmit={save}>
        {error ? <div className="alert error">{error}</div> : null}
        <label className="field">
          <span>显示名</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>部门</span>
          <Select
            aria-label="部门"
            value={dept}
            onChange={setDept}
            disabled={!isPlatform}
            options={[{ value: "", label: "无" }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
          />
        </label>
        {isPlatform ? (
          <div className="field">
            <span>权限</span>
            <AdminToggle on={isAdmin} onChange={setIsAdmin} />
            <span className="hint">关闭后是普通用户；开启后拥有全部管理权限。</span>
          </div>
        ) : null}
        {isPlatform ? (
          <label className="field">
            <span>额度</span>
            <Select
              aria-label="额度"
              value={policy}
              onChange={setPolicy}
              options={[{ value: "", label: "跟随部门" }, ...policies.map((p) => ({ value: p.id, label: p.name }))]}
            />
          </label>
        ) : null}
        {usage ? (
          <div className="field">
            <span>本月用量</span>
            <div className="hint">
              {formatTokens(usage.total.weighted)} · {usage.total.calls} 次调用
            </div>
            {usage.items.map((item) => (
              <div key={item.model} className="hint">
                {item.model}：{formatTokens(item.weighted)} · {item.calls} 次
              </div>
            ))}
          </div>
        ) : null}
        {user.auth === "local" ? (
          <label className="field">
            <span>新密码</span>
            <input type="password" autoComplete="new-password" placeholder="不修改则留空" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        ) : null}
        <div className="field-row">
          <span>停用账号</span>
          <button type="button" className={`switch ${disabled ? "on" : ""}`} role="switch" aria-checked={disabled} aria-label="停用账号" onClick={() => setDisabled(!disabled)}>
            <span className="knob" />
          </button>
        </div>
      </form>
    </Modal>
  );
}

function NewUserDialog({
  departments,
  isPlatform,
  defaultDept,
  onSaved,
  onClose,
}: {
  departments: Department[];
  isPlatform: boolean;
  defaultDept: string;
  onSaved: (user: AdminUser) => void;
  onClose: () => void;
}) {
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [dept, setDept] = useState(defaultDept);
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSaved(await admin.createUser({ username: username.trim(), display_name: name.trim() || username.trim(), password, dept_id: dept || null, roles: isAdmin ? ["user", "platform_admin"] : ["user"] }));
    } catch (err) {
      setError(reason(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title="新建用户"
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="new-user-form" disabled={busy || !username.trim() || password.length < 8}>
            创建
          </button>
        </>
      }
    >
      <form id="new-user-form" className="admin-form" onSubmit={save}>
        {error ? <div className="alert error">{error}</div> : null}
        <label className="field">
          <span>用户名</span>
          <input type="text" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>显示名</span>
          <input type="text" value={name} placeholder={username} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>初始密码</span>
          <input type="password" autoComplete="new-password" placeholder="至少 8 个字符" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="field">
          <span>部门</span>
          <Select
            aria-label="部门"
            value={dept}
            onChange={setDept}
            disabled={!isPlatform}
            options={[{ value: "", label: "无" }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
          />
        </label>
        {isPlatform ? (
          <div className="field">
            <span>权限</span>
            <AdminToggle on={isAdmin} onChange={setIsAdmin} />
            <span className="hint">默认是普通用户；开启后拥有全部管理权限。</span>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}

export default function UsersSection({ me, isPlatform }: { me: Me; isPlatform: boolean }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [dept, setDept] = useState("");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [policies, setPolicies] = useState<QuotaPolicy[]>([]);
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const seq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    admin.departments().then(setDepartments).catch(() => undefined);
    if (isPlatform) admin.policies().then(setPolicies).catch(() => undefined);
  }, [isPlatform]);

  const load = useCallback(
    async (after: string | null) => {
      const mine = ++seq.current;
      try {
        const page = await admin.users({ q, dept_id: dept, cursor: after });
        if (mine !== seq.current) return;
        setUsers((prev) => (after && prev ? [...prev, ...page.items] : page.items));
        setCursor(page.next_cursor);
        setError("");
      } catch (err) {
        if (mine === seq.current) setError(reason(err));
      }
    },
    [q, dept]
  );

  useEffect(() => {
    load(null);
  }, [load]);

  const replace = (user: AdminUser) => setUsers((prev) => prev?.map((u) => (u.id === user.id ? user : u)) || prev);

  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <label className="search">
          <IconSearch size={15} />
          <input type="text" placeholder="搜索用户" aria-label="搜索用户" value={search} onChange={(e) => setSearch(e.target.value)} />
          {search ? (
            <button className="link" onClick={() => setSearch("")} aria-label="清除搜索">
              <IconX size={13} />
            </button>
          ) : null}
        </label>
        {isPlatform && departments.length ? (
          <Select
            aria-label="按部门筛选"
            value={dept}
            onChange={setDept}
            options={[{ value: "", label: "全部部门" }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
          />
        ) : null}
        <span className="spacer" />
        <button className="secondary" onClick={() => setCreating(true)}>
          <IconPlus size={16} /> 新建用户
        </button>
      </div>

      {error ? <div className="alert error">{error}</div> : null}
      {!users ? null : users.length === 0 ? (
        <div className="empty">{q ? `没有匹配「${q}」的用户` : "还没有用户"}</div>
      ) : (
        <div className="admin-list">
          {users.map((u) => (
            <button key={u.id} className={`admin-row user-row ${u.disabled ? "off" : ""}`} onClick={() => setEditing(u)}>
              <span className="avatar">{(u.display_name || u.username || "?")[0].toUpperCase()}</span>
              <span className="grow">
                <span className="name">
                  {u.display_name || u.username}
                  {u.id === me.id ? <span className="you">你</span> : null}
                </span>
                <span className="meta">{u.username || u.email}</span>
              </span>
              <span className="col dept">{u.dept?.name || ""}</span>
              <span className="col roles">
                {u.roles.some((r) => r !== "user") ? <span className="chip">管理者</span> : <span className="chip">普通用户</span>}
              </span>
              <span className="col num" title="本月用量（按模型倍率折算）">
                {formatTokens(u.usage.monthly)}
              </span>
              <span className="col when">{u.disabled ? <span className="status canceled">已停用</span> : u.last_login_at ? timeAgo(u.last_login_at) : "从未登录"}</span>
            </button>
          ))}
        </div>
      )}
      {cursor ? (
        <div className="load-more">
          <button className="link" onClick={() => load(cursor)}>
            加载更多
          </button>
        </div>
      ) : null}

      {editing ? (
        <UserDialog
          user={editing}
          departments={departments}
          policies={policies}
          isPlatform={isPlatform}
          onSaved={(u) => {
            replace(u);
            setEditing(null);
          }}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {creating ? (
        <NewUserDialog
          departments={departments}
          isPlatform={isPlatform}
          defaultDept={isPlatform ? dept : me.dept?.id || ""}
          onSaved={(u) => {
            setUsers((prev) => [u, ...(prev || [])]);
            setCreating(false);
          }}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </section>
  );
}
