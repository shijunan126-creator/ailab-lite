"use client";

import { useState } from "react";
import type { Part } from "../../lib/message-parts";
import { skillTone } from "./rich-input";

/** A sent message: its text with the skill chips where the user put them. A long chip name opens on tap. */
export function SkillChip({ name, title }: { name: string; title: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className={`skill-chip tone-${skillTone(name)} ${open ? "open" : ""}`} title={title} role="button" tabIndex={-1}
      aria-label={`技能 ${title}`} aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="skill-chip-label">{title}</span>
    </span>
  );
}

export default function MessageText({ parts }: { parts: Part[] }) {
  return (
    <>
      {parts.map((p, i) => (p.kind === "text" ? <span key={i}>{p.text}</span> : <SkillChip key={i} name={p.name} title={p.title} />))}
    </>
  );
}
