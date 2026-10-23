// Types and calls of the admin API (/v1/admin). Nothing here reaches another user's conversations or files:
// the API has no such endpoints.
import { api, API_BASE, redirectIfUnauthorized, Ref, Role } from "./api";

export type Page<T> = { items: T[]; next_cursor: string | null };

export type AdminUser = {
  id: string;
  username: string | null;
  display_name: string;
  email: string;
  dept: Ref | null;
  roles: Role[];
  disabled: boolean;
  quota_policy: Ref | null;
  auth: "local" | "oidc";
  created_at: string;
  last_login_at: string | null;
  usage: { daily: number; monthly: number };
};

export type Department = { id: string; name: string; parent_id: string | null; quota_policy: Ref | null; members: number };

export type QuotaPolicy = {
  id: string;
  name: string;
  daily_tokens: number;
  monthly_tokens: number;
  library_bytes: number;
  max_concurrent_runs: number;
  is_default: boolean;
  users: number;
};

export type AuditEntry = {
  id: string;
  at: string;
  actor: Ref | null;
  actor_service: string | null;
  action: string;
  target_type: string;
  target_id: string;
  outcome: string;
  request_id: string;
  detail: Record<string, unknown> | null;
};

export type UsageRow = { key: string; label: string; weighted: number; input_tokens: number; output_tokens: number; calls: number };

export const ROLE_LABEL: Record<Role, string> = {
  user: "成员",
  dept_admin: "部门管理员",
  platform_admin: "平台管理员",
  auditor: "审计员",
};

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** A list the API pages through X-Next-Cursor (departments, quota policies): every page, for pickers. */
async function everyPage<T>(path: string): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 50; page++) {
    const resp: Response = await fetch(`${API_BASE}${path}${query({ limit: 100, cursor })}`, { cache: "no-store" });
    redirectIfUnauthorized(resp);
    if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
    out.push(...((await resp.json()) as T[]));
    cursor = resp.headers.get("x-next-cursor");
    if (!cursor) break;
  }
  return out;
}

export const admin = {
  users: (p: { q?: string; dept_id?: string; cursor?: string | null }) => api<Page<AdminUser>>(`/v1/admin/users${query({ ...p, limit: 50 })}`),
  createUser: (body: { username: string; display_name: string; password: string; dept_id?: string | null; roles?: Role[] }) =>
    api<AdminUser>("/v1/admin/users", { method: "POST", body: JSON.stringify(body) }),
  patchUser: (id: string, body: Partial<{ display_name: string; dept_id: string | null; roles: Role[]; disabled: boolean; quota_policy_id: string | null }>) =>
    api<AdminUser>(`/v1/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setPassword: (id: string, password: string) => api<void>(`/v1/admin/users/${id}/password`, { method: "POST", body: JSON.stringify({ password }) }),
  departments: () => everyPage<Department>("/v1/admin/departments"),
  saveDepartment: (body: { name: string; parent_id?: string | null; quota_policy_id?: string | null }, id?: string) =>
    api<Department>(id ? `/v1/admin/departments/${id}` : "/v1/admin/departments", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) }),
  deleteDepartment: (id: string) => api<void>(`/v1/admin/departments/${id}`, { method: "DELETE" }),
  policies: () => everyPage<QuotaPolicy>("/v1/admin/quota-policies"),
  savePolicy: (body: Omit<QuotaPolicy, "id" | "users">, id?: string) =>
    api<QuotaPolicy>(id ? `/v1/admin/quota-policies/${id}` : "/v1/admin/quota-policies", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) }),
  deletePolicy: (id: string) => api<void>(`/v1/admin/quota-policies/${id}`, { method: "DELETE" }),
  audit: (p: { action?: string; actor_id?: string; cursor?: string | null }) => api<Page<AuditEntry>>(`/v1/admin/audit${query({ ...p, limit: 50 })}`),
  usage: (p: { from: string; to: string; group_by: "dept" | "user" | "model" }) => api<{ rows: UsageRow[] }>(`/v1/admin/usage${query(p)}`),
};

/** "403 {...}" → a sentence for the page. */
export function reason(err: any): string {
  const text = String(err?.message || err);
  if (text.startsWith("403")) return "没有权限";
  if (text.startsWith("409")) return "仍在使用中，不能删除";
  const detail = text.replace(/^\d{3}\s*/, "");
  try {
    const d = JSON.parse(detail)?.detail;
    return typeof d === "string" ? d : detail;
  } catch {
    return detail;
  }
}
