"use client";

import Link from "next/link";
import { useEffect } from "react";

/** A page that failed to render keeps the sidebar: the rest of the app still works, and retrying renders it again. */
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="lost">
      <div>
        <h1>页面出错了</h1>
        <p>这个页面没能显示出来，其他功能不受影响。</p>
        <div className="lost-actions">
          <button onClick={reset}>重试</button>
          <Link className="btn secondary" href="/">
            新对话
          </Link>
        </div>
      </div>
    </div>
  );
}
