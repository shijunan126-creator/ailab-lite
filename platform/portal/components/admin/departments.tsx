"use client";

import { FormEvent, useEffect, useState } from "react";
import { admin, Department, QuotaPolicy, reason } from "../../lib/admin";
import { IconPlus, IconTrash } from "../icons";
import Modal from "../library/modal";
import { confirmDialog } from "../../lib/dialog";
import Select from "../select";

function DepartmentDialog({
  dept,
  departments,
  policies,
  onSaved,
  onClose,
}: {
  dept: Department | null;
  departments: Department[];
  policies: QuotaPolicy[];
  onSaved: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(dept?.name || "");
  const [parent, setParent] = useState(dept?.parent_id || "");
  const [policy, setPolicy] = useState(dept?.quota_policy?.id || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await admin.saveDepartment({ name: name.trim(), parent_id: parent || null, quota_policy_id: policy || null }, dept?.id);
      onSaved();
    } catch (err) {
      setError(reason(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={dept ? dept.name : "新建部门"}
      className="modal-sm"
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="dept-form" disabled={busy || !name.trim()}>
            {dept ? "保存" : "创建"}
          </button>
        </>
      }
    >
      <form id="dept-form" className="admin-form" onSubmit={save}>
        {error ? <div className="alert error">{error}</div> : null}
        <label className="field">
          <span>名称</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>上级部门</span>
          <Select
            aria-label="上级部门"
            value={parent}
            onChange={setParent}
            options={[{ value: "", label: "无" }, ...departments.filter((d) => d.id !== dept?.id).map((d) => ({ value: d.id, label: d.name }))]}
          />
        </label>
        <label className="field">
          <span>额度</span>
          <Select
            aria-label="额度"
            value={policy}
            onChange={setPolicy}
            options={[{ value: "", label: "默认" }, ...policies.map((p) => ({ value: p.id, label: p.name }))]}
          />
        </label>
      </form>
    </Modal>
  );
}

export default function DepartmentsSection() {
  const [departments, setDepartments] = useState<Department[] | null>(null);
  const [policies, setPolicies] = useState<QuotaPolicy[]>([]);
  const [editing, setEditing] = useState<Department | "new" | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    admin
      .departments()
      .then(setDepartments)
      .catch((err) => setError(reason(err)));

  useEffect(() => {
    load();
    admin.policies().then(setPolicies).catch(() => undefined);
  }, []);

  async function remove(d: Department) {
    if (!(await confirmDialog({ title: `删除部门「${d.name}」？`, action: "删除", danger: true }))) return;
    try {
      await admin.deleteDepartment(d.id);
      load();
    } catch (err) {
      setError(reason(err));
    }
  }

  const names = new Map((departments || []).map((d) => [d.id, d.name]));
  return (
    <section className="admin-section">
      <div className="admin-toolbar">
        <span className="spacer" />
        <button className="secondary" onClick={() => setEditing("new")}>
          <IconPlus size={16} /> 新建部门
        </button>
      </div>
      {error ? <div className="alert error">{error}</div> : null}
      {!departments ? null : departments.length === 0 ? (
        <div className="empty">还没有部门</div>
      ) : (
        <div className="admin-list">
          {departments.map((d) => (
            <div key={d.id} className="admin-row">
              <button className="grow plain" onClick={() => setEditing(d)}>
                <span className="name">{d.name}</span>
                {d.parent_id ? <span className="meta">{names.get(d.parent_id)}</span> : null}
              </button>
              <span className="col">{d.quota_policy?.name || "默认额度"}</span>
              <span className="col num">{d.members} 人</span>
              <button className="icon-btn danger-hover" onClick={() => remove(d)} disabled={d.members > 0} title={d.members ? "部门还有成员" : "删除"} aria-label={`删除 ${d.name}`}>
                <IconTrash size={16} />
              </button>
            </div>
          ))}
        </div>
      )}
      {editing ? (
        <DepartmentDialog
          dept={editing === "new" ? null : editing}
          departments={departments || []}
          policies={policies}
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
