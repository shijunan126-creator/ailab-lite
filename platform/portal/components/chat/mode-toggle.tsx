"use client";

import { useEffect, useState } from "react";

export type Mode = "chat" | "work";

const MODE_HINT: Record<Mode, string> = {
  chat: "问答、搜索、写作",
  work: "动手完成任务，交付文件",
};

/** The 对话/工作 choice. It is remembered per browser, so the state lives above the composer that reads it. */
export function useChatMode(): [Mode, (next: Mode) => void] {
  const [mode, setModeState] = useState<Mode>("work");
  useEffect(() => {
    if (localStorage.getItem("ailab.mode") === "chat") setModeState("chat");
  }, []);
  function setMode(next: Mode) {
    setModeState(next);
    localStorage.setItem("ailab.mode", next);
  }
  return [mode, setMode];
}

export default function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (next: Mode) => void }) {
  return (
    <div className="mode-toggle" role="radiogroup" aria-label="模式" data-mode={mode}>
      {(["chat", "work"] as Mode[]).map((m) => (
        <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} title={MODE_HINT[m]} onClick={() => onChange(m)}>
          {m === "chat" ? "对话" : "工作"}
        </button>
      ))}
    </div>
  );
}
