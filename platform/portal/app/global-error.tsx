"use client";

import "./globals.css";

/** The root layout itself failed: nothing else on the page is left, so this renders its own document. */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="zh-CN">
      <body>
        <main className="lost">
          <div>
            <h1>页面出错了</h1>
            <p>刷新页面通常可以恢复。</p>
            <button onClick={reset}>重试</button>
          </div>
        </main>
      </body>
    </html>
  );
}
