"use client";

import { useState } from "react";
import type { Part } from "../../lib/message-parts";
import { splitMath } from "../../lib/math";
import TexMath from "../math";
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

/** Plain text with its formulas typeset; everything else stays exactly as typed. */
function TextWithMath({ text }: { text: string }) {
  const pieces = splitMath(text);
  if (pieces.length === 1 && typeof pieces[0] === "string") return <span>{text}</span>;
  return <span>{pieces.map((p, i) => (typeof p === "string" ? p : <TexMath key={i} tex={p.tex} display={p.display} raw={p.raw} />))}</span>;
}

/** `math` typesets formulas in the text: on for a message read in full, off where it is cut to one line. */
export default function MessageText({ parts, math = false }: { parts: Part[]; math?: boolean }) {
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "text" ? (
          math ? <TextWithMath key={i} text={p.text} /> : <span key={i}>{p.text}</span>
        ) : (
          <SkillChip key={i} name={p.name} title={p.title} />
        )
      )}
    </>
  );
}
