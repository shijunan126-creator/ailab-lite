"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FileItem, ModelInfo, Skill, SkillKind, api, skillKind } from "../../lib/api";
import { MAX_SKILLS, matchSkills, plainText, skillsOf, toPayload, type Part } from "../../lib/message-parts";
import type { SendOptions } from "../../lib/outbox";
import { canRetry, existingUpload, forgetUpload, retryUpload, startUpload, useUploads } from "../../lib/uploads";
import { usePopover } from "../../lib/use-popover";
import AttachmentChip from "./attachment-chip";
import { IconArrowUp, IconCheck, IconChevronRight, IconFileText, IconLibrary, IconPlus, IconQueue, IconSearch, IconSheet, IconSlides, IconSparkles, IconStop, IconUpload } from "../icons";
import LibraryPicker from "../library/library-picker";
import ContextMeter, { estimateTokens } from "./context-meter";
import type { Mode } from "./mode-toggle";
import ModelMenu from "./model-menu";
import RichInput, { skillTone, type RichInputHandle, type SlashState } from "./rich-input";
import TemplatePicker, { TemplateChoice } from "./template-picker";

export type Attachment = Pick<FileItem, "id" | "filename" | "status" | "content_type" | "size">;
export type { SendOptions };
/** What the composer hands over: the message, frozen at the moment of sending (HLD §16.2). */
export type Outgoing = { prompt: string; attachments: string[]; options: SendOptions; steer: boolean };

const LINE = 24; // px: the message box's line height
const VISIBLE_SKILLS = 3; // in the + menu; the rest behind 更多技能, searchable once the list is long
const SEARCH_FROM = 8;

/** A skill as the composer offers it. */
export type Offered = { name: string; title: string; kind: SkillKind; fileId: string | null; template: boolean };

const ICONS: Record<string, (p: { size?: number }) => React.ReactElement> = {
  "platform.pptx": IconSlides,
  pptx: IconSlides,
  "platform.xlsx": IconSheet,
  "platform.docx": IconFileText,
  "platform.pdf": IconFileText,
};
export const skillIcon = (name: string) => ICONS[name] || IconSparkles;

// What the + menu shows first: the user's own and optional skills; the four office skills work without being picked
const RANK: Record<SkillKind, number> = { builtin: 0, personal: 1, base: 2 };

export function offeredSkills(list: Skill[]): Offered[] {
  return list
    .filter((s) => s.enabled && !s.implicit && !s.error)
    .map((s) => ({
      name: s.name,
      title: s.title || s.name,
      kind: skillKind(s),
      fileId: skillKind(s) === "personal" ? s.file_id || null : null,
      template: s.extras ? s.extras.includes("ppt_template") : s.name === "pptx" || s.name === "platform.pptx",
    }));
}

export default function Composer({
  models,
  mode,
  onMode: _onMode,
  modeLocked: _modeLocked,
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
  // an existing conversation keeps the mode it started with (a fork is how it changes); skills work in both modes
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
  const [parts, setParts] = useState<Part[]>([]);
  const [files, setFiles] = useState<string[]>([]); // upload keys, in the order attached
  const uploads = useUploads((st) => st.uploads);
  const menu = usePopover();
  const [menuView, setMenuView] = useState<"main" | "skills">("main");
  const [skillQuery, setSkillQuery] = useState("");
  const [library, setLibrary] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [dragging, setDragging] = useState(false);
  // the PPT template belongs to the chip it was chosen for: removing that chip drops it
  const [template, setTemplateState] = useState<{ owner: string; choice: TemplateChoice } | null>(null);
  const input = useRef<RichInputHandle | null>(null);

  const [skills, setSkills] = useState<Offered[]>([]);
  const [slash, setSlash] = useState<SlashState>(null);
  const [slashAt, setSlashAt] = useState(0); // highlighted entry of the "/" list
  const [slashClosed, setSlashClosed] = useState(false); // Escape: this "/" stays text

  useEffect(() => {
    api<Skill[]>("/v1/skills")
      .then((list) => setSkills(offeredSkills(list).sort((a, b) => RANK[a.kind] - RANK[b.kind])))
      .catch(() => setSkills([]));
  }, []);

  useEffect(() => {
    if (!menu.open) {
      setMenuView("main");
      setSkillQuery("");
    }
  }, [menu.open]);

  const text = plainText(parts);
  const chips = skillsOf(parts);
  const inMessage = new Set(chips.map((c) => c.name));
  // the template goes with a PPT chip, and only Work can use a template file
  const templateOwner = mode === "work" ? chips.find((c) => skills.find((s) => s.name === c.name)?.template)?.name ?? null : null;
  const activeTemplate = template && template.owner === templateOwner ? template.choice : null;
  useEffect(() => {
    if (template && template.owner !== templateOwner) setTemplateState(null);
  }, [template, templateOwner]);

  function addSkill(s: Offered, fromSlash: boolean) {
    setError("");
    if (inMessage.has(s.name)) {
      if (fromSlash) input.current?.completeSlash(null);
      input.current?.flash(s.name);
      return;
    }
    if (chips.length >= MAX_SKILLS) {
      if (fromSlash) input.current?.completeSlash(null);
      setError(`一条消息最多 ${MAX_SKILLS} 个技能`);
      return;
    }
    const ref = { name: s.name, title: s.title, fileId: s.fileId };
    if (fromSlash) input.current?.completeSlash(ref);
    else input.current?.insertSkill(ref);
  }

  function toggleFromMenu(s: Offered) {
    menu.setOpen(false);
    if (inMessage.has(s.name)) input.current?.removeSkill(s.name);
    else addSkill(s, false);
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

  // One line when empty; past a line the text takes the whole first row and the controls move under it. The box
  // itself grows to four lines and then scrolls (max-height in CSS); measured before paint.
  const expandedAt = useRef(0); // the text length that made the box grow: it shrinks again only well below it
  useLayoutEffect(() => {
    const el = input.current?.element();
    if (!el) return;
    if (!parts.length) {
      if (expanded) setExpanded(false);
      return;
    }
    const oneLine = el.scrollHeight <= LINE * 1.5 + 12;
    const size = text.length + chips.length * 6;
    if (!expanded && (text.includes("\n") || !oneLine)) {
      expandedAt.current = size;
      setExpanded(true);
    } else if (expanded && !text.includes("\n") && size < expandedAt.current * 0.85 && oneLine) setExpanded(false);
  }, [parts, text, chips.length, expanded]);

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

  const templateWaiting = !!activeTemplate?.pending;
  const canSend = !!text.trim() && !busy && !templateWaiting && !!model;
  const queueing = running && !!queueable;

  const shown = skills.slice(0, VISIBLE_SKILLS);
  const found = skillQuery.trim() ? matchSkills(skills, skillQuery) : skills;
  // "/" at the start or after a space lists the skills; with no match the list says so and Enter sends as usual
  const slashItems: Offered[] = slash && !slashClosed ? matchSkills(skills, slash.query) : [];
  const slashOpen = !!slash && !slashClosed && (slashItems.length > 0 || !!slash.query);
  const slashIndex = Math.min(slashAt, Math.max(0, slashItems.length - 1));

  async function send(steer = false) {
    if (!canSend || (running && !queueable)) return;
    if (overImages) { setError("每轮最多附加5张图片"); return; }
    const { prompt, skills: picks } = toPayload(parts);
    const options: SendOptions = { mode, ...(picks.length ? { skills: picks } : {}), ...(activeTemplate ? { template: activeTemplate.ref } : {}) };
    const message: Outgoing = { prompt, attachments: files, options, steer };
    // the message is handed over and the box is free for the next one at once; its files go with it
    const ok = await onSubmit(message);
    if (ok) {
      input.current?.clear();
      setFiles([]);
      setTemplateState(null); // skills and their template are for one message; the mode stays
      setError("");
    }
  }

  const skillItem = (s: Offered) => {
    const Icon = skillIcon(s.name);
    const on = inMessage.has(s.name);
    return (
      <button key={s.name} type="button" role="menuitemcheckbox" aria-checked={on} className={`menu-item ${on ? "on" : ""}`} onClick={() => toggleFromMenu(s)}>
        <Icon size={16} /> <span className="grow skill-menu-name">{s.title}</span>
        {on ? <IconCheck size={14} /> : null}
      </button>
    );
  };

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
      {templateOwner ? (
        <div className="skill-tag-row">
          <TemplatePicker value={activeTemplate} onChange={(choice) => setTemplateState(choice ? { owner: templateOwner, choice } : null)} projectId={projectId} />
        </div>
      ) : null}
      {slashOpen ? (
        <div className="slash-menu" id="slash-menu" role="listbox" aria-label="技能">
          {slashItems.length ? (
            slashItems.map((s, i) => {
              const Icon = skillIcon(s.name);
              return (
                <button key={s.name} type="button" role="option" aria-selected={i === slashIndex} className={`slash-item ${i === slashIndex ? "on" : ""} ${inMessage.has(s.name) ? "picked" : ""}`}
                  onMouseMove={() => setSlashAt(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => addSkill(s, true)}>
                  <span className={`slash-icon tone-${skillTone(s.name)}`}><Icon size={15} /></span>
                  <span className="slash-label">{s.title}</span>
                  {inMessage.has(s.name) ? <IconCheck size={14} /> : null}
                </button>
              );
            })
          ) : (
            <div className="menu-empty" role="presentation">没有匹配的技能</div>
          )}
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
        <RichInput
          ref={input}
          className="composer-input"
          ariaLabel="消息"
          autoFocus={autoFocus}
          placeholder={placeholder || "发消息…"}
          onChange={(next) => {
            setParts(next);
            if (error) setError("");
          }}
          onSlash={(next) => {
            setSlash(next);
            setSlashAt(0);
            setSlashClosed(false);
          }}
          slashOpen={slashOpen}
          controls={slashOpen ? "slash-menu" : undefined}
          onPasteFiles={upload}
          onKeyDown={(e) => {
            if (slashOpen && !e.nativeEvent.isComposing) {
              if (slashItems.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
                e.preventDefault();
                setSlashAt((slashIndex + (e.key === "ArrowDown" ? 1 : slashItems.length - 1)) % slashItems.length);
                return;
              }
              if (slashItems.length && (e.key === "Enter" || e.key === "Tab")) {
                e.preventDefault();
                addSkill(slashItems[slashIndex], true);
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
              send(queueing && e.altKey && !activeTemplate && !chips.length); // skills and templates start their own answer
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
