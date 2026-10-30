"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FileItem, ModelInfo, Skill, api } from "../../lib/api";
import type { SendOptions } from "../../lib/outbox";
import { canRetry, existingUpload, forgetUpload, retryUpload, startUpload, useUploads } from "../../lib/uploads";
import { usePopover } from "../../lib/use-popover";
import AttachmentChip from "./attachment-chip";
import { IconArrowUp, IconChart, IconChevronRight, IconLibrary, IconPlus, IconQueue, IconSearch, IconSheet, IconSlides, IconSparkles, IconStop, IconUpload, IconX } from "../icons";
import LibraryPicker from "../library/library-picker";
import ContextMeter, { estimateTokens } from "./context-meter";
import type { Mode } from "./mode-toggle";
import ModelMenu from "./model-menu";
import TemplatePicker, { TemplateChoice } from "./template-picker";

export type Attachment = Pick<FileItem, "id" | "filename" | "status" | "content_type" | "size">;
export type { SendOptions };
/** What the composer hands over: the message, frozen at the moment of sending (HLD §16.2). */
export type Outgoing = { prompt: string; attachments: string[]; options: SendOptions; steer: boolean };

const LINE = 24; // px: the textarea's line height
const MAX_LINES = 4; // then the text scrolls inside the box instead of pushing the answer away

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

const VISIBLE_SKILLS = 3; // in the + menu; the rest behind 更多技能, searchable once the list is long
const SEARCH_FROM = 8;


export default function Composer({
  models,
  mode,
  onMode,
  modeLocked,
  model,
  onModel,
  busy,
  running,
  queueable,
  onSubmit,
  onStop,
  autoFocus,
  placeholder,
  projectId,
  initialFiles,
  context,
}: {
  mode: Mode;
  onMode: (next: Mode) => void;
  // an existing conversation keeps the mode it started with (a fork is how it changes), so skills that need Work
  // are offered only where they can run
  modeLocked?: boolean;
  models: ModelInfo[];
  model: string;
  onModel: (id: string) => void;
  busy: boolean;
  running: boolean;
  // while an answer runs the message waits for it (steer: or goes into it at its next step)
  queueable?: boolean;
  onSubmit: (message: Outgoing) => boolean | Promise<boolean>;
  onStop: () => void;
  autoFocus?: boolean;
  placeholder?: string;
  projectId?: string | null; // uploads from here go into this project
  initialFiles?: Attachment[]; // e.g. "talk about these" from the library
  context?: { used: number | null; compactAt: number | null }; // the conversation so far, for the context meter
}) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<string[]>([]); // upload keys, in the order attached
  const uploads = useUploads((st) => st.uploads);
  const menu = usePopover();
  const [menuView, setMenuView] = useState<"main" | "skills">("main");
  const [skillQuery, setSkillQuery] = useState("");
  const [library, setLibrary] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const expandedAt = useRef(0); // the text length that made the box grow: it shrinks again only well below it
  const [dragging, setDragging] = useState(false);
  const [picked, setPickedState] = useState<SkillButton | null>(null);
  const [template, setTemplate] = useState<TemplateChoice | null>(null); // only with the PPT skill; none by default
  const area = useRef<HTMLTextAreaElement | null>(null);

  const [enabled, setEnabled] = useState<Set<string> | null>(null); // skills switched on in the 技能 page
  const [mine, setMine] = useState<Skill[]>([]); // the user's own skills (personal, switched on)
  const [builtin, setBuiltin] = useState<Skill[]>([]); // platform skills switched on
  const [slashAt, setSlashAt] = useState(0); // highlighted entry of the "/" menu
  const [slashClosed, setSlashClosed] = useState(false); // Escape: the "/" stays text

  useEffect(() => {
    api<Skill[]>("/v1/skills")
      .then((list) => {
        setEnabled(new Set(list.filter((s) => s.enabled).map((s) => s.name)));
        setMine(list.filter((s) => s.scope === "personal" && s.enabled));
        setBuiltin(list.filter((s) => s.scope === "builtin" && s.enabled && !s.implicit));
      })
      .catch(() => setEnabled(null));
  }, []);

  // skills make files, which is Work: a new conversation switches to it, a Chat conversation does not offer them
  const skillsOffered = !modeLocked || mode === "work";

  function setPicked(next: SkillButton | null) {
    setPickedState(next);
    if (next?.skill !== "pptx") setTemplate(null);
  }

  useEffect(() => {
    if (mode === "chat") setPickedState(null);
  }, [mode]);

  useEffect(() => {
    if (!menu.open) {
      setMenuView("main");
      setSkillQuery("");
    }
  }, [menu.open]);

  function pick(s: SkillButton) {
    menu.setOpen(false);
    setPicked(picked?.key === s.key ? null : s);
    if (mode === "chat" && !modeLocked) onMode("work");
    area.current?.focus();
  }
  const picker = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (initialFiles?.length) attachExisting(initialFiles);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialFiles]);

  function attachExisting(list: Attachment[]) {
    setFiles((prev) => [...prev, ...list.map((f) => `file:${f.id}`).filter((key) => !prev.includes(key))]);
    for (const f of list) existingUpload(f);
  }

  // One line when empty; grows with the text up to MAX_LINES, then scrolls inside; shrinks as the text is deleted.
  // Measured before paint, so the box never shows a frame at the wrong height.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    if (!text) {
      el.style.height = "";
      el.style.overflowY = "hidden";
      if (expanded) setExpanded(false);
      return;
    }
    el.style.height = "auto";
    const pad = el.offsetHeight - el.clientHeight + parseFloat(getComputedStyle(el).paddingTop) + parseFloat(getComputedStyle(el).paddingBottom);
    const max = LINE * MAX_LINES + pad;
    const full = el.scrollHeight + (el.offsetHeight - el.clientHeight);
    el.style.height = `${Math.min(full, max)}px`;
    el.style.overflowY = full > max ? "auto" : "hidden";
    const wraps = text.includes("\n") || full > LINE * 1.5 + pad;
    if (!expanded && wraps) {
      expandedAt.current = text.length;
      setExpanded(true);
    } else if (expanded && !text.includes("\n") && text.length < expandedAt.current * 0.85 && full <= LINE * 1.5 + pad) setExpanded(false);
  }, [text, expanded]);

  const isImage = (key: string) => {
    const u = uploads[key];
    return !!u && (u.contentType.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|tiff?|avif)$/i.test(u.filename));
  };
  const imageCount = files.filter(isImage).length;
  const overImages = imageCount > 5;
  const selectedModel = models.find((m) => m.id === model);

  function upload(list: FileList | File[]) {
    setError("");
    const picked = Array.from(list);
    if (imageCount + picked.filter((f) => f.type.startsWith("image/")).length > 5) {
      setError("每轮最多附加5张图片");
      return;
    }
    // uploads start now and go on in the background; sending does not wait for them
    const keys = picked.map((file) => startUpload(file, projectId));
    setFiles((prev) => [...prev, ...keys]);
    if (picker.current) picker.current.value = "";
  }

  function removeFile(key: string) {
    setFiles((prev) => prev.filter((k) => k !== key));
    forgetUpload(key);
  }

  const templateWaiting = !!template?.pending;
  const canSend = !!text.trim() && !busy && !templateWaiting && !!model;
  const queueing = running && !!queueable;

  const skills: SkillButton[] = skillsOffered
    ? [
        ...SKILLS.filter((s) => !enabled || enabled.has(s.skill)),
        ...mine.map((m) => ({ key: `my-${m.name}`, skill: m.name, label: m.title, Icon: IconSparkles, placeholder: `说说要做什么，会按你的「${m.title}」技能来做` })),
        ...builtin
          .filter((b) => !SKILLS.some((s) => s.skill === b.name))
          .map((b) => ({ key: `skill-${b.name}`, skill: b.name, label: b.title, Icon: IconSparkles, placeholder: b.description || `说说要做什么，会按「${b.title}」技能来做` })),
      ]
    : [];
  const shown = skills.slice(0, VISIBLE_SKILLS);
  const found = skillQuery.trim() ? skills.filter((s) => `${s.label} ${s.skill}`.toLowerCase().includes(skillQuery.trim().toLowerCase())) : skills;
  // "/" at the start of an empty message lists the skills; picking one fills in what it needs
  const slashQuery = /^\/(\S*)$/.exec(text)?.[1];
  const slashItems: SkillButton[] =
    slashQuery === undefined || slashClosed ? [] : skills.filter((b) => !slashQuery || `${b.label} ${b.skill}`.toLowerCase().includes(slashQuery.toLowerCase()));
  const slashIndex = Math.min(slashAt, Math.max(0, slashItems.length - 1));

  function choose(s: SkillButton) {
    setPicked(s);
    if (mode === "chat" && !modeLocked) onMode("work");
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
    if (!canSend || (running && !queueable)) return;
    if (overImages) { setError("每轮最多附加5张图片"); return; }
    const options: SendOptions = picked ? { mode: "work", skill: picked.skill, ...(template ? { template: template.ref } : {}) } : { mode };
    const message: Outgoing = { prompt: text.trim(), attachments: files, options, steer };
    // the message is handed over and the box is free for the next one at once; its files go with it
    const ok = await onSubmit(message);
    if (ok) {
      setText("");
      setFiles([]);
      setPicked(null); // a skill is for one message; the mode stays
      setError("");
    }
  }

  const skillItem = (s: SkillButton) => (
    <button key={s.key} type="button" role="menuitemradio" aria-checked={picked?.key === s.key} className={`menu-item ${picked?.key === s.key ? "on" : ""}`} onClick={() => pick(s)}>
      <s.Icon size={16} /> <span className="grow truncate">{s.label}</span>
    </button>
  );

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
        <div className="attach-row composer-attachments">
          {files.map((key) => (
            <AttachmentChip key={key} uploadKey={key} onRemove={() => removeFile(key)} onRetry={canRetry(key) ? () => retryUpload(key, projectId) : undefined} />
          ))}
        </div>
      ) : null}
      {picked ? (
        <div className="skill-tag-row">
          <span className="skill-tag">
            <picked.Icon size={14} /> <span className="truncate">{picked.label}</span>
            <button type="button" className="skill-tag-x" aria-label={`取消 ${picked.label}`} onClick={() => setPicked(null)}>
              <IconX size={12} />
            </button>
          </span>
          {picked.skill === "pptx" ? <TemplatePicker value={template} onChange={setTemplate} projectId={projectId} /> : null}
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
      <div className={`composer-main ${expanded ? "expanded" : ""}`}>
        <div className="plus-menu" ref={menu.box}>
          <button type="button" className="icon-btn plus" onClick={() => menu.setOpen(!menu.open)} title="添加" aria-label="添加附件" aria-haspopup="menu" aria-expanded={menu.open}>
            <IconPlus size={18} />
          </button>
          {menu.open ? (
            <div className={`pop-menu composer-menu ${menuView}`} role="menu" aria-label={menuView === "skills" ? "全部技能" : "添加"}>
              {menuView === "main" ? (
                <>
                  <button type="button" className="menu-item" role="menuitem" onClick={() => { menu.setOpen(false); picker.current?.click(); }}>
                    <IconUpload size={16} /> <span className="grow">上传文件</span>
                  </button>
                  <button type="button" className="menu-item" role="menuitem" onClick={() => { menu.setOpen(false); setLibrary(true); }}>
                    <IconLibrary size={16} /> <span className="grow">从资料库选择</span>
                  </button>
                  {shown.length ? <div className="menu-sep" role="separator" /> : null}
                  {shown.map(skillItem)}
                  {skills.length > shown.length ? (
                    <button type="button" className="menu-item" role="menuitem" aria-label="更多技能" onClick={() => setMenuView("skills")}>
                      <IconSparkles size={16} /> <span className="grow">更多技能</span>
                      <span className="menu-count">{skills.length - shown.length}</span>
                      <IconChevronRight size={14} />
                    </button>
                  ) : null}
                </>
              ) : (
                <>
                  <div className="menu-head">
                    <button type="button" className="icon-btn sm" aria-label="返回" onClick={() => setMenuView("main")}>
                      <span className="flip"><IconChevronRight size={14} /></span>
                    </button>
                    {skills.length >= SEARCH_FROM ? (
                      <label className="menu-search">
                        <IconSearch size={14} />
                        <input type="text" autoFocus placeholder="搜索技能" aria-label="搜索技能" value={skillQuery} onChange={(e) => setSkillQuery(e.target.value)} />
                      </label>
                    ) : <span className="menu-title">技能</span>}
                  </div>
                  <div className="menu-scroll">
                    {found.length ? found.map(skillItem) : <div className="menu-empty">没有匹配的技能</div>}
                  </div>
                </>
              )}
            </div>
          ) : null}
        </div>
        <input ref={picker} type="file" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        {library ? (
          <LibraryPicker
            projectId={projectId}
            attached={files.flatMap((key) => (uploads[key]?.fileId ? [uploads[key].fileId!] : []))}
            onClose={() => setLibrary(false)}
            onPick={(picked: FileItem[]) => {
              setLibrary(false);
              attachExisting(picked);
            }}
          />
        ) : null}
        <textarea
          ref={area}
          rows={1}
          autoFocus={autoFocus}
          aria-label="消息"
          placeholder={picked?.placeholder || placeholder || "发消息…"}
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
              send(queueing && e.altKey && !template); // a message with a template starts its own answer
            }
          }}
        />
        <span className="composer-right">
          {imageCount > 0 && !selectedModel?.capabilities?.includes("vision") ? <span className="hint" title="当前模型将通过 OCR 读取图片文字">将用 OCR 读图</span> : null}
          {error ? <span className="error hint" title={error}>{error}</span> : null}
          {(() => {
            const limit = selectedModel?.input_budget_tokens || selectedModel?.context_tokens;
            return limit ? <ContextMeter used={context?.used ?? null} limit={limit} compactAt={context?.compactAt ?? null} draft={estimateTokens(text)} /> : null;
          })()}
          <ModelMenu models={models} value={model} onChange={onModel} />
          {queueing && text.trim() ? (
            <button type="button" className="send-btn queue" onClick={() => send(false)} disabled={!canSend} aria-label="排队发送" title="回答完成后发送（⌥↵ 立即插入）">
              <IconQueue size={16} />
            </button>
          ) : null}
          {running ? (
            <button type="button" className="send-btn stop" onClick={onStop} aria-label="停止" title="停止">
              <IconStop size={14} />
            </button>
          ) : (
            <button type="button" className="send-btn" onClick={() => send()} disabled={!canSend} aria-label="发送" title="发送">
              <IconArrowUp size={18} />
            </button>
          )}
        </span>
      </div>
    </div>
  );
}
