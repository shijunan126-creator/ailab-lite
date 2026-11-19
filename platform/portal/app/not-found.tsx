import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "页面不存在 · AILab" };

export default function NotFound() {
  return (
    <main className="lost">
      <div>
        <h1>页面不存在</h1>
        <p>链接可能已失效，或者内容已被删除。</p>
        <Link className="btn" href="/">
          回到首页
        </Link>
      </div>
    </main>
  );
}
