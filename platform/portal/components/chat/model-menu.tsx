"use client";

import { useState } from "react";
import { ModelInfo } from "../../lib/api";
import { usePopover } from "../../lib/use-popover";
import { IconCheck, IconChevronDown, IconImage } from "../icons";

/** The model and its thinking strength for the next message, in one menu: models on the left, the levels of the model
 * under the pointer on the right (each model keeps its own level). */
export default function ModelMenu({
  models,
  value,
  levelOf,
  onChange,
}: {
  models: ModelInfo[];
  value: string;
  levelOf: (modelId: string) => string | undefined; // the level a model would run with now
  onChange: (id: string, level?: string) => void;
}) {
  const { open, setOpen, box } = usePopover();
  const [shown, setShown] = useState(value); // whose levels the right column shows
  const current = models.find((m) => m.id === value);
  if (!models.length) return null;
  const level = current?.reasoning?.levels.find((l) => l.id === levelOf(current.id));
  const target = models.find((m) => m.id === shown) || current;
  const thinking = models.some((m) => m.reasoning);
  const pick = (id: string, next?: string) => {
    onChange(id, next);
    setOpen(false);
  };
  return (
    <div className="model-menu" ref={box}>
      <button
        className="model-btn"
        onClick={() => {
          setShown(value);
          setOpen(!open);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="模型"
      >
        <span className="model-btn-name">{current?.name || "选择模型"}</span>
        {level ? <span className="model-btn-level">{level.name}</span> : null}
        <IconChevronDown size={12} />
      </button>
      {open ? (
        <div className={`pop-menu right-up model-pop ${thinking ? "split" : ""}`}>
          <div className="model-list" role="listbox" aria-label="模型">
            {models.map((m) => (
              <button
                key={m.id}
                role="option"
                aria-selected={m.id === value}
                className={`menu-item model-item ${m.id === target?.id ? "shown" : ""}`}
                title={m.description || undefined}
                onMouseEnter={() => setShown(m.id)}
                onFocus={() => setShown(m.id)}
                onClick={() => pick(m.id)}
              >
                <span className="grow">{m.name}</span>
                {m.capabilities?.includes("vision") ? <span role="img" aria-label="支持图片" title="支持图片"><IconImage size={14} /></span> : null}
                {m.weight !== 1 ? <span className="model-weight">×{m.weight}</span> : null}
                {m.id === value ? <IconCheck size={14} /> : <span className="model-check-gap" />}
              </button>
            ))}
          </div>
          {thinking ? (
            <div className="model-levels" role="listbox" aria-label="思考强度">
              <div className="model-levels-head" aria-hidden="true">思考强度</div>
              {target?.reasoning ? (
                target.reasoning.levels.map((l) => {
                  const on = l.id === levelOf(target.id); // the level this model runs with, picked now or last time
                  return (
                    <button key={l.id} role="option" aria-selected={on} className="menu-item model-level" onClick={() => pick(target.id, l.id)}>
                      <span className="grow">{l.name}</span>
                      {on ? <IconCheck size={14} /> : <span className="model-check-gap" />}
                    </button>
                  );
                })
              ) : (
                <div className="model-levels-none">—</div>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
