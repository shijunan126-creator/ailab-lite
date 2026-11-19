"use client";

import { FormEvent, useEffect, useState } from "react";
import {Me, api, errorMessage} from "../../../lib/api";

export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Me>("/v1/auth/me").then(setMe).catch(() => setMe(null));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (next !== again) return setError("两次输入的新密码不一致");
    setBusy(true);
    try {
      const resp = await fetch("/api/v1/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({})))?.detail;
        throw new Error(resp.status === 403 ? "当前密码不对" : typeof detail === "string" ? detail : `修改失败（${resp.status}）`);
      }
      // Changing the password ended every session, this one too: sign in again with the new one.
      await fetch("/api/auth/logout", { method: "POST" });
      window.location.href = "/login";
    } catch (err: any) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <main className="page mpage account-page">
      <div className="mpage-head">
        <h1>账号设置</h1>
      </div>

      <section className="msection">
        <div className="mlist">
          <div className="mrow">
            <span className="mrow-main"><span className="mrow-title"><span>用户名</span></span></span>
            <span className="account-value">{me?.username || "-"}</span>
          </div>
          <div className="mrow">
            <span className="mrow-main"><span className="mrow-title"><span>邮箱</span></span></span>
            <span className="account-value">{me?.email || "-"}</span>
          </div>
        </div>
      </section>

      {me?.username ? (
        <section className="msection">
          <div className="msection-head"><h2>修改密码</h2></div>
          <form className="mlist account-form" onSubmit={submit}>
            {error ? <div className="alert error" role="alert">{error}</div> : null}
            <label className="field">
              <span>当前密码</span>
              <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
            </label>
            <label className="field">
              <span>新密码</span>
              <input type="password" autoComplete="new-password" placeholder="至少 8 个字符" value={next} onChange={(e) => setNext(e.target.value)} />
            </label>
            <label className="field">
              <span>再输入一次新密码</span>
              <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
            </label>
            <div className="account-foot">
              <span className="hint">修改后，所有设备都要用新密码重新登录</span>
              <button type="submit" disabled={busy || !current || !next || !again}>
                {busy ? "提交中…" : "修改密码"}
              </button>
            </div>
          </form>
        </section>
      ) : me ? (
        <p className="hint account-note">这个账号没有设置密码登录（使用 API 令牌），无法在这里修改密码。</p>
      ) : null}
    </main>
  );
}
