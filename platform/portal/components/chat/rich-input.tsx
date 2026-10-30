"use client";

// The message box: text with skill chips inside it. A contenteditable div owned by the browser, not by React — the
// DOM is the source of truth while typing, so IME composition, the caret and native undo keep working. Every change
// goes through execCommand (insertText / insertHTML / delete), which is what puts it on the undo stack; React only
// reads the DOM back into parts after each input.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { normalize, slashQuery, type Part, type SkillRef } from "../../lib/message-parts";

export type RichInputHandle = {
  focus: (atEnd?: boolean) => void;
  parts: () => Part[];
  setParts: (parts: Part[]) => void;
  clear: () => void;
  /** Put a skill at the caret (or at the end when the box was not focused). */
  insertSkill: (skill: SkillRef) => void;
  /** Replace the "/query" being typed with this skill, or just remove it (null). */
  completeSlash: (skill: SkillRef | null) => void;
  /** Remove every chip of this skill, as an undoable edit. */
  removeSkill: (name: string) => void;
  /** Briefly highlight the chip of a skill already in the message. */
  flash: (name: string) => void;
  element: () => HTMLDivElement | null;
};

export type SlashState = { query: string } | null;

const MIME = "application/x-ailab-parts";
const X_SVG =
  '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** A stable colour per skill, from the chart series: the same skill always looks the same. */
export function skillTone(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.codePointAt(0)!) >>> 0;
  return (h % 6) + 1;
}

function chipHtml(s: SkillRef): string {
  return (
    `<span class="skill-chip tone-${skillTone(s.name)}" contenteditable="false" data-skill="${esc(s.name)}" data-title="${esc(s.title)}"` +
    (s.fileId ? ` data-file="${esc(s.fileId)}"` : "") +
    ` title="${esc(s.title)}"><span class="skill-chip-label">${esc(s.title)}</span>` +
    `<button type="button" tabindex="-1" class="skill-chip-x" aria-label="移除 ${esc(s.title)}">${X_SVG}</button></span>`
  );
}

function partsHtml(parts: Part[]): string {
  return parts.map((p) => (p.kind === "text" ? esc(p.text) : chipHtml(p))).join("");
}

const isChip = (n: Node | null): n is HTMLElement => !!n && n.nodeType === 1 && (n as HTMLElement).dataset.skill !== undefined;
const isBlock = (n: Node) => n.nodeType === 1 && /^(DIV|P|LI)$/.test((n as HTMLElement).tagName);

/** The DOM read back into parts. A trailing <br> is the browser's placeholder for an empty last line, not text. */
export function readParts(root: Node): Part[] {
  const out: Part[] = [];
  const walk = (node: Node) => {
    node.childNodes.forEach((child, i) => {
      if (child.nodeType === 3) out.push({ kind: "text", text: (child.nodeValue || "").replace(/ /g, " ") });
      else if (isChip(child)) {
        const d = (child as HTMLElement).dataset;
        out.push({ kind: "skill", name: d.skill!, title: d.title || d.skill!, fileId: d.file || null });
      } else if ((child as HTMLElement).tagName === "BR") {
        if (!(i === node.childNodes.length - 1 && node === root)) out.push({ kind: "text", text: "\n" });
      } else if (child.nodeType === 1) {
        // blocks some mobile keyboards insert: each one starts a new line
        if (isBlock(child) && out.length) out.push({ kind: "text", text: "\n" });
        walk(child);
      }
    });
  };
  walk(root);
  return normalize(out);
}

/** Text from the last boundary (start, line break, chip) up to the caret, plus the caret position. */
function textBeforeCaret(root: HTMLElement): { before: string; node: Text; offset: number } | null {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || !sel.isCollapsed) return null;
  const node = sel.anchorNode;
  if (!node || node.nodeType !== 3 || !root.contains(node)) return null;
  let before = (node.nodeValue || "").slice(0, sel.anchorOffset);
  for (let prev = node.previousSibling; prev && prev.nodeType === 3; prev = prev.previousSibling) before = (prev.nodeValue || "") + before;
  return { before: before.replace(/ /g, " "), node: node as Text, offset: sel.anchorOffset };
}

/** A range covering the last `length` UTF-16 units before (node, offset), across adjacent text nodes. */
function rangeBack(node: Text, offset: number, length: number): Range {
  const range = document.createRange();
  range.setEnd(node, offset);
  let cur: Node = node;
  let left = length;
  let at = offset;
  while (left > at && cur.previousSibling && cur.previousSibling.nodeType === 3) {
    left -= at;
    cur = cur.previousSibling;
    at = (cur.nodeValue || "").length;
  }
  range.setStart(cur, Math.max(0, at - left));
  return range;
}

function caretToEnd(root: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(root);
  range.collapse(false);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

type Props = {
  ariaLabel: string;
  placeholder?: string;
  autoFocus?: boolean;
  initial?: Part[];
  className?: string;
  onChange?: (parts: Part[]) => void;
  onSlash?: (state: SlashState) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
  onPasteFiles?: (files: File[]) => void;
  slashOpen?: boolean; // for aria-expanded
  controls?: string;
};

const RichInput = forwardRef<RichInputHandle, Props>(function RichInput(
  { ariaLabel, placeholder, autoFocus, initial, className, onChange, onSlash, onKeyDown, onPasteFiles, slashOpen, controls },
  ref,
) {
  const box = useRef<HTMLDivElement | null>(null);
  const composing = useRef(false);
  const lastSlash = useRef<string | null>(null);
  const handlers = useRef({ onChange, onSlash });
  handlers.current = { onChange, onSlash };

  const sync = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const parts = readParts(el);
    el.classList.toggle("is-empty", parts.length === 0);
    handlers.current.onChange?.(parts);
  }, []);

  const checkSlash = useCallback(() => {
    const el = box.current;
    if (!el || composing.current) return;
    const at = document.activeElement === el ? textBeforeCaret(el) : null;
    const hit = at ? slashQuery(at.before) : null;
    const key = hit ? hit.query : null;
    if (key === lastSlash.current) return;
    lastSlash.current = key;
    handlers.current.onSlash?.(hit ? { query: hit.query } : null);
  }, []);

  const exec = (command: string, value?: string) => {
    box.current?.focus();
    document.execCommand(command, false, value);
  };

  // removing a chip is an edit like any other: select it and delete, so ⌘Z brings it back
  const deleteChip = (chip: Element) => {
    box.current?.focus();
    const range = document.createRange();
    range.selectNode(chip);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    document.execCommand("delete");
  };

  const insertParts = (parts: Part[]) => {
    if (parts.every((p) => p.kind === "text")) exec("insertText", parts.map((p) => (p.kind === "text" ? p.text : "")).join(""));
    else exec("insertHTML", partsHtml(parts));
  };

  useImperativeHandle(ref, () => ({
    focus: (atEnd) => {
      const el = box.current;
      if (!el) return;
      el.focus();
      if (atEnd) caretToEnd(el);
    },
    parts: () => (box.current ? readParts(box.current) : []),
    setParts: (parts) => {
      const el = box.current;
      if (!el) return;
      el.innerHTML = partsHtml(normalize(parts));
      sync();
    },
    clear: () => {
      const el = box.current;
      if (!el) return;
      el.textContent = "";
      lastSlash.current = null;
      sync();
    },
    insertSkill: (skill) => {
      const el = box.current;
      if (!el) return;
      const sel = window.getSelection();
      if (document.activeElement !== el || !sel?.rangeCount || !el.contains(sel.anchorNode)) {
        el.focus();
        caretToEnd(el);
      }
      const before = textBeforeCaret(el)?.before ?? "";
      const pad = before && !/\s$/.test(before) ? " " : "";
      exec("insertHTML", esc(pad) + chipHtml(skill) + " ");
    },
    completeSlash: (skill) => {
      const el = box.current;
      if (!el) return;
      const at = textBeforeCaret(el);
      const hit = at ? slashQuery(at.before) : null;
      if (at && hit) {
        const sel = window.getSelection()!;
        sel.removeAllRanges();
        sel.addRange(rangeBack(at.node, at.offset, hit.length));
        if (skill) exec("insertHTML", chipHtml(skill) + " ");
        else exec("delete");
      } else if (skill) exec("insertHTML", chipHtml(skill) + " ");
      lastSlash.current = null;
      handlers.current.onSlash?.(null);
    },
    removeSkill: (name) => {
      for (const chip of Array.from(box.current?.querySelectorAll<HTMLElement>(".skill-chip") || [])) if (chip.dataset.skill === name) deleteChip(chip);
    },
    flash: (name) => {
      const chip = Array.from(box.current?.querySelectorAll<HTMLElement>(".skill-chip") || []).find((c) => c.dataset.skill === name);
      if (!chip) return;
      chip.classList.remove("flash");
      void chip.offsetWidth; // restart the animation
      chip.classList.add("flash");
    },
    element: () => box.current,
  }));

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (initial?.length) el.innerHTML = partsHtml(normalize(initial));
    el.classList.toggle("is-empty", !initial?.length);
    if (autoFocus) {
      el.focus();
      caretToEnd(el);
    }
    // Enter on some mobile keyboards asks for a new paragraph: keep the message one block with line breaks
    const beforeInput = (e: InputEvent) => {
      if (e.inputType === "insertParagraph") {
        e.preventDefault();
        document.execCommand("insertLineBreak");
      }
    };
    const selection = () => {
      if (document.activeElement === el) checkSlash();
    };
    el.addEventListener("beforeinput", beforeInput);
    document.addEventListener("selectionchange", selection);
    return () => {
      el.removeEventListener("beforeinput", beforeInput);
      document.removeEventListener("selectionchange", selection);
    };
    // mounted once: `initial` is the starting content, later changes go through setParts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function copy(e: React.ClipboardEvent<HTMLDivElement>, cut: boolean) {
    const sel = window.getSelection();
    if (!sel?.rangeCount || sel.isCollapsed) return;
    const holder = document.createElement("div");
    holder.appendChild(sel.getRangeAt(0).cloneContents());
    const parts = readParts(holder);
    e.preventDefault();
    e.clipboardData.setData("text/plain", parts.map((p) => (p.kind === "text" ? p.text : `/${p.title}`)).join(""));
    e.clipboardData.setData(MIME, JSON.stringify(parts));
    if (cut) document.execCommand("delete");
  }

  return (
    <div
      ref={box}
      className={`rich-input is-empty ${className || ""}`}
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      aria-label={ariaLabel}
      aria-expanded={slashOpen}
      aria-controls={controls}
      data-placeholder={placeholder || ""}
      spellCheck={false}
      onInput={() => {
        sync();
        checkSlash();
      }}
      onCompositionStart={() => {
        composing.current = true;
      }}
      onCompositionEnd={() => {
        composing.current = false;
        sync();
        checkSlash();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.shiftKey && !e.nativeEvent.isComposing) {
          e.preventDefault();
          document.execCommand("insertLineBreak");
          return;
        }
        onKeyDown?.(e);
      }}
      onMouseDown={(e) => {
        const target = e.target as HTMLElement;
        const x = target.closest(".skill-chip-x");
        if (x) {
          e.preventDefault();
          deleteChip(x.closest(".skill-chip")!);
          return;
        }
        const chip = target.closest(".skill-chip");
        if (chip) chip.classList.toggle("open"); // long names: tap to read all of it
      }}
      onPaste={(e) => {
        const files = Array.from(e.clipboardData.files);
        if (files.length) {
          e.preventDefault();
          onPasteFiles?.(files);
          return;
        }
        e.preventDefault();
        const own = e.clipboardData.getData(MIME);
        if (own) {
          try {
            insertParts(normalize(JSON.parse(own) as Part[]));
            return;
          } catch {
            // fall back to the plain text
          }
        }
        exec("insertText", e.clipboardData.getData("text/plain"));
      }}
      onCopy={(e) => copy(e, false)}
      onCut={(e) => copy(e, true)}
      onDrop={(e) => {
        if (e.dataTransfer.types.includes("Files")) return; // the composer takes files as attachments
        e.preventDefault();
        const text = e.dataTransfer.getData("text/plain");
        if (!text) return;
        const at = document.caretRangeFromPoint?.(e.clientX, e.clientY);
        if (at) {
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(at);
        }
        exec("insertText", text);
      }}
    />
  );
});

export default RichInput;
