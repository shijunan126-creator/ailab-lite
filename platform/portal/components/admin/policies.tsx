"use client";

import { FormEvent, useEffect, useState } from "react";
import { admin, QuotaPolicy, reason } from "../../lib/admin";
import { formatBytes, formatTokens } from "../../lib/format";
import { IconPlus, IconTrash } from "../icons";
import Modal from "../library/modal";
import { confirmDialog } from "../../lib/dialog";

const GB = 1024 ** 3;
const BLANK = { name: "", daily_tokens: 200_000, monthly_tokens: 4_000_000, library_bytes: 20 * GB, max_concurrent_runs: 2, is_default: false };

function Num({ label, value, onChange, unit }: { label: string; value: number; onChange: (n: number) => void; unit?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <span className="input-unit">
        <input type="number" min={0} inputMode="numeric" value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(Number(e.target.value))} />
        {unit ? <em>{unit}</em> : null}
      </span>
    </label>
  );
}

function PolicyDialog({ policy, onSaved, onClose }: { policy: QuotaPolicy | null; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState(() => (policy ? { ...policy } : { ...BLANK }));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const { name, daily_tokens, monthly_tokens, library_bytes, max_concurrent_runs, is_default } = form;
      await admin.savePolicy({ name: name.trim(), daily_tokens, monthly_tokens, library_bytes, max_concurrent_runs, is_default }, policy?.id);
      onSaved();
    } catch (err) {
      setError(reason(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={policy ? policy.name : "新建额度"}
      className="modal-sm"
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="policy-form" disabled={busy || !form.name.trim()}>
            {policy ? "保存" : "创建"}
          </button>
        </>
      }
    >
      <form id="policy-form" className="admin-form" onSubmit={save}>
        {error ? <div className="alert error">{error}</div> : null}
        <label className="field">
          <span>名称</span>
          <input type="text" value={form.name} onChange={(e) => set({ name: e.target.value })} autoFocus />
        </label>
        <Num label="每日 Token" value={form.daily_tokens} onChange={(n) => set({ daily_tokens: n })} />
        <Num label="每月 Token" value={form.monthly_tokens} onChange={(n) => set({ monthly_tokens: n })} />
        <Num label="资料库空间" unit="GB" value={Math.round((form.library_bytes / GB) * 10) / 10} onChange={(n) => set({ library_bytes: Math.round(n * GB) })} />
        <Num label="同时运行的任务" value={form.max_concurrent_runs} onChange={(n) => set({ max_concurrent_runs: n })} />
        <div className="field-row">
          <span>设为默认</span>
          <button type="button" className={`switch ${form.is_default ? "on" : ""}`} role="switch" aria-checked={form.is_default} aria-label="设为默认" onClick={() => set({ is_default: !form.is_default })}>
            <span className="knob" />
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function PoliciesSection() {
  const [policies, setPolicies] = useState<QuotaPolicy[] | null>(null);
  const [editing, setEditing] = useState<QuotaPolicy | "new" | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    admin
      .policies()
      .then(setPolicies)
      .catch((err) => setError(reason(err)));
  useEffect(() => {
    load();
  }, []);

  async function remove(p: QuotaPolicy) {
    if (!(await confirmDialog({ title: `删除额度「${p.name}」？`, action: "删除", danger: true }))) return;
    try {
      await admin.deletePolicy(p.id);
      load();
    } catch (err) {
      setError(reason(err));
    }
  }

  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <span className="spacer" />
        <button className="secondary" onClick={() => setEditing("new")}>
          <IconPlus size={16} /> 新建额度
        </button>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {policies?.length === 0 ? <div className="empty">还没有额度策略，所有人使用系统默认值</div> : null}
      <div className="policy-grid">
        {(policies || []).map((p) => (
          <div key={p.id} className="policy-card">
            <button className="policy-open" onClick={() => setEditing(p)}>
              <span className="policy-name">
                {p.name}
                {p.is_default ? <span className="chip">默认</span> : null}
              </span>
              <dl className="policy-limits">
                <dt>每日</dt>
                <dd>{formatTokens(p.daily_tokens)}</dd>
                <dt>每月</dt>
                <dd>{formatTokens(p.monthly_tokens)}</dd>
                <dt>空间</dt>
                <dd>{formatBytes(p.library_bytes)}</dd>
                <dt>并发</dt>
                <dd>{p.max_concurrent_runs}</dd>
              </dl>
            </button>
            <div className="policy-foot">
              <span>{p.users} 人使用</span>
              <button className="icon-btn danger-hover" disabled={p.is_default || p.users > 0} onClick={() => remove(p)} aria-label={`删除 ${p.name}`} title="删除">
                <IconTrash size={15} />
              </button>
            </div>
          </div>
        ))}
      </div>
      {editing ? (
        <PolicyDialog
          policy={editing === "new" ? null : editing}
          onSaved={() => {
            setEditing(null);
            load();
          }}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}
