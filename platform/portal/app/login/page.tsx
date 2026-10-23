"use client";

import { FormEvent, useEffect, useState } from "react";
import AuthBrand from "../../components/auth-brand";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [registration, setRegistration] = useState("closed");
  const [sso, setSso] = useState<{ enabled: boolean; label: string } | null>(null);
  const [passwords, setPasswords] = useState(true);

  useEffect(() => {
    fetch("/api/auth/options")
      .then((r) => r.json())
      .then((o) => {
        setRegistration(o.registration || "closed");
        setSso(o.oidc?.enabled ? o.oidc : null);
        setPasswords(o.password !== false);
      })
      .catch(() => undefined);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const resp = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({})))?.detail;
        throw new Error(resp.status === 401 ? "用户名或密码错误" : typeof detail === "string" ? detail : `登录失败（${resp.status}）`);
      }
      // The proxy (proxy.ts) serves this page at the URL the visitor asked for; go back there.
      const asked = window.location.pathname === "/login" ? new URLSearchParams(window.location.search).get("next") || "/" : window.location.pathname;
      window.location.href = asked.startsWith("/") && !asked.startsWith("//") ? asked : "/";
    } catch (err: any) {
      setError(String(err?.message || err));
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <AuthBrand />

      <main className="auth-form-side">
        <form className="auth-form" onSubmit={submit}>
          <h1>登录</h1>
          {error ? <div className="alert error" role="alert">{error}</div> : null}
          {sso ? (
            <a className="btn lg sso" href="/api/auth/oidc/start">
              使用{sso.label || "统一身份"}登录
            </a>
          ) : null}
          {sso && passwords ? <div className="auth-or">或</div> : null}
          {passwords ? (
            <>
              <label className="field">
                <span>用户名</span>
                <input type="text" name="username" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
              </label>
              <label className="field">
                <span>密码</span>
                <input type="password" name="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
              <button type="submit" className={`lg ${sso ? "secondary" : ""}`} disabled={busy || !username.trim() || !password}>
                {busy ? "登录中…" : "登录"}
              </button>
            </>
          ) : null}
          <p className="hint" style={{ marginTop: 16 }}>
            {registration !== "closed" && passwords ? (
              <>
                还没有账号？<a href="/register">注册一个</a>
                {registration === "invite" ? "（需要邀请码）" : ""}。忘记密码请联系管理员。
              </>
            ) : (
              "没有账号或忘记密码，请联系管理员。"
            )}
          </p>
        </form>
      </main>
    </div>
  );
}
