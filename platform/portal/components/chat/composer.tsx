"use client";

import { useEffect, useRef, useState } from "react";
import { api, FileItem, filesByIds, ModelInfo, Skill, uploadFile } from "../../lib/api";
import { IconArrowUp, IconChart, IconFile, IconLibrary, IconPlus, IconQueue, IconSheet, IconSlides, IconSparkles, IconStop, IconUpload } from "../icons";
import LibraryPicker from "../library/library-picker";
import ContextMeter, { estimateTokens } from "./context-meter";

export type Attachment = { id: string; filename: string; status: string };
export type Mode = "chat" | "work";
export type SendOptions = { mode: Mode; skill?: string };

type SkillButton = {
  key: string;
  skill: string;
  label: string;
  Icon: (p: { size?: number }) => React.ReactElement;
  placeholder: string;
  template?: string; // what "/" fills in: the questions this skill needs answered
};

// What the skill buttons ask for. `skill` is the platform skill that gets preloaded for the model.
const SKILLS: SkillButton[] = [
  { key: "ppt", skill: "pptx", label: "PPT 生成", Icon: IconSlides, placeholder: "说说主题、页数和给谁看，比如：给新员工介绍公司的 10 页 PPT", template: "主题：\n页数：\n给谁看：" },
  { key: "excel", skill: "xlsx", label: "Excel 表格", Icon: IconSheet, placeholder: "说说表里要有什么，比如：三个区域 7～9 月销售额，带合计和柱状图", template: "表格内容：\n时间范围：\n需要的图表：" },
  { key: "data", skill: "xlsx", label: "数据分析", Icon: IconChart, placeholder: "上传数据文件（Excel / CSV），说说想分析什么、要什么图表", template: "想分析的问题：\n关注的指标：" },
];

const MODE_HINT: Record<Mode, string> = {
  chat: "问答、搜索、写作",
  work: "动手完成任务，交付文件",
};

export default function Composer({
  models,
  model,
  onModel,
  busy,
  running,
  onSend,
  onQueue,
  onStop,
  autoFocus,
  placeholder,
  projectId,
  initialFiles,
  context,
}: {
  models: ModelInfo[];
  model: string;
  onModel: (id: string) => void;
  busy: boolean;
  running: boolean;
  onSend: (prompt: string, fileIds: string[], options: SendOptions) => Promise<boolean>;
  // while an answer runs: the message waits for it (steer: or goes into it at its next step)
  onQueue?: (prompt: string, fileIds: string[], options: SendOptions, steer: boolean) => Promise<boolean>;
  onStop: () => void;
  autoFocus?: boolean;
  placeholder?: string;
  projectId?: string | null; // uploads from here go into this project
  initialFiles?: Attachment[]; // e.g. "talk about these" from the library
  context?: { used: number | null; compactAt: number | null }; // the conversation so far, for the context meter
}) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [menu, setMenu] = useState(false);
  const [library, setLibrary] = useState(false);
  const menuBox = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [mode, setModeState] = useState<Mode>("work");
  const [picked, setPicked] = useState<SkillButton | null>(null);
  const area = useRef<HTMLTextAreaElement | null>(null);

  const [enabled, setEnabled] = useState<Set<string> | null>(null); // skills switched on in the 技能 page
  const [mine, setMine] = useState<Skill[]>([]); // the user's own skills (personal, switched on)
  const [builtin, setBuiltin] = useState<Skill[]>([]); // platform skills switched on
  const [slashAt, setSlashAt] = useState(0); // highlighted entry of the "/" menu
  const [slashClosed, setSlashClosed] = useState(false); // Escape: the "/" stays text

  useEffect(() => {
    if (localStorage.getItem("ailab.mode") === "chat") setModeState("chat");
    api<Skill[]>("/v1/skills")
      .then((list) => {
        setEnabled(new Set(list.filter((s) => s.enabled).map((s) => s.name)));
        setMine(list.filter((s) => s.scope === "personal" && s.enabled));
        setBuiltin(list.filter((s) => s.scope === "builtin" && s.enabled));
      })
      .catch(() => setEnabled(null));
  }, []);

  function setMode(next: Mode) {
    setModeState(next);
    localStorage.setItem("ailab.mode", next);
    if (next === "chat") setPicked(null); // a skill makes files: that is work
  }

  function pick(s: SkillButton) {
    setPicked(picked?.key === s.key ? null : s);
    if (mode === "chat") setMode("work");
    area.current?.focus();
  }
  const picker = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (initialFiles?.length) setFiles((prev) => [...prev, ...initialFiles.filter((f) => !prev.some((x) => x.id === f.id))]);
  }, [initialFiles]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => menuBox.current && !menuBox.current.contains(e.target as Node) && setMenu(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  // grow with the text, up to a limit
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(220, el.scrollHeight)}px`;
  }, [text]);

  // uploads are quarantined until the Worker has scanned them
  useEffect(() => {
    if (!files.some((f) => f.status === "pending")) return;
    let stale = false;
    const timer = setInterval(async () => {
      const fresh: FileItem[] = await filesByIds(files.filter((f) => f.status === "pending").map((f) => f.id)).catch(() => []);
      const status = new Map(fresh.map((x) => [x.id, x.status] as const));
      // only update chips still on the message: a stale answer must not bring back one the user removed
      if (!stale) setFiles((current) => current.map((f) => (status.has(f.id) ? { ...f, status: status.get(f.id)! } : f)));
    }, 1500);
    return () => {
      stale = true;
      clearInterval(timer);
    };
  }, [files]);

  async function upload(list: FileList | File[]) {
    setError("");
    setUploading(true);
    try {
      for (const file of Array.from(list)) {
        try {
          const item = await uploadFile(file, projectId);
          setFiles((prev) => [...prev, { id: item.id, filename: item.filename, status: item.status }]);
        } catch (err: any) {
          setError(String(err?.message || err));
        }
      }
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setUploading(false);
      if (picker.current) picker.current.value = "";
    }
  }

  const waiting = files.some((f) => f.status === "pending");
  const canSend = !!text.trim() && !busy && !running && !waiting && !uploading && !!model;
  const queueing = running && !!onQueue;
  const canQueue = queueing && !!text.trim() && !busy && !waiting && !uploading && !!model;

  // "/" at the start of an empty message lists the skills; picking one fills in what it needs
  const buttons: SkillButton[] = [
    ...SKILLS.filter((s) => !enabled || enabled.has(s.skill)),
    ...mine.map((m) => ({ key: `my-${m.name}`, skill: m.name, label: m.title, Icon: IconSparkles, placeholder: `说说要做什么，会按你的「${m.title}」技能来做` })),
  ];
  const slashQuery = /^\/(\S*)$/.exec(text)?.[1];
  const slashItems: SkillButton[] =
    slashQuery === undefined || slashClosed
      ? []
      : [
          ...buttons,
          ...builtin
            .filter((b) => !SKILLS.some((s) => s.skill === b.name))
            .map((b) => ({ key: `skill-${b.name}`, skill: b.name, label: b.title, Icon: IconSparkles, placeholder: b.description || `说说要做什么，会按「${b.title}」技能来做` })),
        ].filter((b) => !slashQuery || `${b.label} ${b.skill}`.toLowerCase().includes(slashQuery.toLowerCase()));
  const slashIndex = Math.min(slashAt, Math.max(0, slashItems.length - 1));

  function choose(s: SkillButton) {
    setPicked(s);
    if (mode === "chat") setMode("work");
    const template = s.template || "";
    setText(template);
    requestAnimationFrame(() => {
      const el = area.current;
      if (!el) return;
      el.focus();
      const end = template.indexOf("\n");
      const caret = end < 0 ? template.length : end;
      el.setSelectionRange(caret, caret);
    });
  }

  async function send(steer = false) {
    if (queueing ? !canQueue : !canSend) return;
    const ready = files.filter((f) => f.status === "ready").map((f) => f.id);
    const options: SendOptions = picked ? { mode: "work", skill: picked.skill } : { mode };
    const ok = queueing ? await onQueue!(text.trim(), ready, options, steer) : await onSend(text.trim(), ready, options);
    if (ok) {
      setText("");
      setFiles([]);
      setPicked(null); // a skill is for one message; the mode stays
    }
  }

  return (
    <div
      className={`composer-box ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        upload(e.dataTransfer.files);
      }}
    >
      {files.length ? (
        <div className="attach-row">
          {files.map((f) => (
            <span key={f.id} className={`attach-chip ${f.status}`}>
              {f.status === "pending" ? <span className="chip-spin" aria-hidden="true" /> : <IconFile size={13} />} {f.filename}
              {f.status === "pending" ? <span className="chip-state">检查中</span> : f.status === "rejected" ? <span className="chip-state">未通过检查</span> : null}
              <button className="link" aria-label={`移除 ${f.filename}`} onClick={() => setFiles(files.filter((x) => x.id !== f.id))}>
                ✕
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {picked ? (
        <div className="skill-tag-row">
          <span className="skill-tag">
            <picked.Icon size={14} /> {picked.label}
            <button className="link" aria-label={`取消 ${picked.label}`} onClick={() => setPicked(null)}>
              ✕
            </button>
          </span>
        </div>
      ) : null}
      {slashItems.length ? (
        <div className="slash-menu" id="slash-menu" role="listbox" aria-label="技能">
          {slashItems.map((s, i) => (
            <button key={s.key} type="button" role="option" aria-selected={i === slashIndex} className={`slash-item ${i === slashIndex ? "on" : ""}`}
              onMouseMove={() => setSlashAt(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(s)}>
              <span className="slash-icon"><s.Icon size={15} /></span>
              <span className="slash-label">{s.label}</span>
            </button>
          ))}
        </div>
      ) : null}
      <textarea
        ref={area}
        rows={1}
        autoFocus={autoFocus}
        aria-label="消息"
        placeholder={picked?.placeholder || placeholder || "输入消息，Enter 发送，Shift + Enter 换行"}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setSlashAt(0);
          if (!e.target.value.startsWith("/")) setSlashClosed(false);
        }}
        aria-expanded={slashItems.length > 0}
        aria-controls={slashItems.length ? "slash-menu" : undefined}
        onPaste={(e) => {
          // a screenshot or a copied file goes in as an attachment, text pastes as usual
          const pasted = Array.from(e.clipboardData.files);
          if (!pasted.length) return;
          e.preventDefault();
          upload(pasted);
        }}
        onKeyDown={(e) => {
          if (slashItems.length && !e.nativeEvent.isComposing) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setSlashAt((slashIndex + (e.key === "ArrowDown" ? 1 : slashItems.length - 1)) % slashItems.length);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              choose(slashItems[slashIndex]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setSlashClosed(true);
              return;
            }
          }
          // isComposing: Enter that confirms a Chinese IME candidate must not send the message
          // Alt/Option+Enter while an answer runs: hand the message to that answer now
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send(queueing && e.altKey);
          }
        }}
      />
      <div className="composer-tools">
        <div className="plus-menu" ref={menuBox}>
          <button className="icon-btn plus" disabled={uploading} onClick={() => setMenu(!menu)} title="添加附件" aria-label="添加附件" aria-expanded={menu}>
            <IconPlus size={16} />
          </button>
          {menu ? (
            <div className="pop-menu" role="menu">
              <button
                className="menu-item"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  picker.current?.click();
                }}
              >
                <IconUpload size={16} /> 上传文件
              </button>
              <button
                className="menu-item"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  setLibrary(true);
                }}
              >
                <IconLibrary size={16} /> 从资料库选择
              </button>
            </div>
          ) : null}
        </div>
        <div className="mode-toggle" role="radiogroup" aria-label="模式" data-mode={mode}>
          {(["chat", "work"] as Mode[]).map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} title={MODE_HINT[m]} onClick={() => setMode(m)}>
              {m === "chat" ? "对话" : "工作"}
            </button>
          ))}
        </div>
        {mode === "work"
          ? buttons.map((s) => (
              <button
                key={s.key}
                className={`skill-chip ${picked?.key === s.key ? "on" : ""}`}
                onClick={() => pick(s)}
                aria-pressed={picked?.key === s.key}
                aria-label={s.label}
                title={s.label}
              >
                <s.Icon size={14} /> <span className="chip-label">{s.label}</span>
              </button>
            ))
          : null}
        <input ref={picker} type="file" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        {library ? (
          <LibraryPicker
            projectId={projectId}
            attached={files.map((f) => f.id)}
            onClose={() => setLibrary(false)}
            onPick={(picked: FileItem[]) => {
              setLibrary(false);
              setFiles((prev) => [...prev, ...picked.filter((f) => !prev.some((x) => x.id === f.id)).map((f) => ({ id: f.id, filename: f.filename, status: f.status }))]);
            }}
          />
        ) : null}
        <span className="spacer" />
        <span className="composer-right">
        {(() => {
          const info = models.find((m) => m.id === model);
          const limit = info?.input_budget_tokens || info?.context_tokens;
          return limit ? <ContextMeter used={context?.used ?? null} limit={limit} compactAt={context?.compactAt ?? null} draft={estimateTokens(text)} /> : null;
        })()}
        <select value={model} aria-label="模型" onChange={(e) => onModel(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
              {m.weight !== 1 ? `（消耗 ×${m.weight}）` : ""}
            </option>
          ))}
        </select>
        {error ? <span className="error hint">{error}</span> : null}
        {queueing && text.trim() ? (
          <button className="send-btn queue" onClick={() => send(false)} disabled={!canQueue} aria-label="排队发送" title="回答完成后发送（⌥↵ 立即插入）">
            <IconQueue size={16} />
          </button>
        ) : null}
        {running ? (
          <button className="send-btn stop" onClick={onStop} aria-label="停止" title="停止">
            <IconStop size={14} />
          </button>
        ) : (
          <button className="send-btn" onClick={() => send()} disabled={!canSend} aria-label="发送" title="发送">
            {uploading ? <span className="chip-spin light" aria-hidden="true" /> : <IconArrowUp size={18} />}
          </button>
        )}
        </span>
      </div>
    </div>
  );
}
