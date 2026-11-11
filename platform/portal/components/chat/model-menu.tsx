"use client";

import { ModelInfo } from "../../lib/api";
import { usePopover } from "../../lib/use-popover";
import { IconCheck, IconChevronDown, IconImage } from "../icons";

/** The model and its thinking strength for the next message, in one menu: models on the left, the levels of the chosen
 * model on the right (each model keeps its own level). Picking a model or a level leaves the menu open; a click
 * elsewhere or Escape closes it. */
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
  if (!models.length) return null;
  const current = models.find((m) => m.id === value);
  const level = current?.reasoning?.levels.find((l) => l.id === levelOf(current.id));
  const thinking = models.some((m) => m.reasoning);
  // The levels column keeps the height of the tallest levels list: the menu grows from its bottom edge, so a shorter
  // column would pull the model rows up from under the pointer that just picked one.
  const levelRows = Math.max(0, ...models.map((m) => m.reasoning?.levels.length ?? 0));
  const shownRows = current?.reasoning?.levels.length || 1; // the "—" of a model without levels is one row
  // The right column follows the model this menu has chosen, never the row under the pointer: a hover that resizes the
  // menu moves the rows, the pointer lands on the other model, and the choice flips back and forth.
  const choose = (id: string, next?: string) => {
    onChange(id, next);
    if (!thinking) setOpen(false); // a plain list has nothing else to pick
  };
  return (
    <div className="model-menu" ref={box}>
      <button
        className="model-btn"
        onClick={() => setOpen(!open)}
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
                className="menu-item model-item"
                title={m.description || undefined}
                onClick={() => choose(m.id)}
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
              {current?.reasoning ? (
                current.reasoning.levels.map((l) => {
                  const on = l.id === levelOf(current.id); // the level this model runs with, picked now or last time
                  return (
                    <button key={l.id} role="option" aria-selected={on} className="menu-item model-level" onClick={() => choose(current.id, l.id)}>
                      <span className="grow">{l.name}</span>
                      {on ? <IconCheck size={14} /> : <span className="model-check-gap" />}
                    </button>
                  );
                })
              ) : (
                <div className="menu-item model-level model-levels-none">—</div>
              )}
              {Array.from({ length: Math.max(0, levelRows - shownRows) }, (_, i) => (
                <div key={`gap-${i}`} className="menu-item model-level model-levels-gap" aria-hidden="true" />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
