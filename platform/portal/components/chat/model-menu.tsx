"use client";

import { ModelInfo } from "../../lib/api";
import { usePopover } from "../../lib/use-popover";
import { IconCheck, IconChevronDown, IconImage } from "../icons";

/** The model for the next message, as an in-page menu rather than the browser's select. */
export default function ModelMenu({ models, value, onChange }: { models: ModelInfo[]; value: string; onChange: (id: string) => void }) {
  const { open, setOpen, box } = usePopover();
  const current = models.find((m) => m.id === value);
  if (!models.length) return null;
  return (
    <div className="model-menu" ref={box}>
      <button className="model-btn" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open} aria-label="模型">
        <span className="model-btn-name">{current?.name || "选择模型"}</span>
        <IconChevronDown size={12} />
      </button>
      {open ? (
        <div className="pop-menu right-up" role="listbox" aria-label="模型">
          {models.map((m) => (
            <button
              key={m.id}
              role="option"
              aria-selected={m.id === value}
              className="menu-item model-item"
              title={m.description || undefined}
              onClick={() => {
                onChange(m.id);
                setOpen(false);
              }}
            >
              <span className="grow">{m.name}</span>
              {m.capabilities?.includes("vision") ? <span role="img" aria-label="支持图片" title="支持图片"><IconImage size={14} /></span> : null}
              {m.weight !== 1 ? <span className="model-weight">×{m.weight}</span> : null}
              {m.id === value ? <IconCheck size={14} /> : <span className="model-check-gap" />}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
