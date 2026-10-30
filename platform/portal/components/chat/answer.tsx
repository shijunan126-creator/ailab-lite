"use client";

import { memo, useMemo } from "react";
import { splitAnswerParts } from "../../lib/answer-parts";
import { saveBlock, startingState, type UIState } from "../../lib/ui-state";
import type { Source } from "../../lib/work-process";
import { GenUIBlock } from "../genui";
import { MAX_BLOCKS } from "../genui/catalog/limits";
import { BlockStatesProvider, type BlockStates } from "../genui/host";
import Markdown from "../markdown";

type Props = {
  text: string;
  streaming?: boolean;
  sources?: Source[];
  virtual?: boolean;
  onOpenLink?: (url: string, title: string) => void;
  /** The run this answer belongs to and its saved UI state; `saveState` writes changes back (owner pages only).
   * Flat props, not an object: the answer is memoised and must not re-render with every parent render. */
  runId?: string;
  uiState?: UIState | null;
  saveState?: boolean;
};

/** An answer: Markdown, with any ```openui blocks rendered as UI in place (HLD §6.3). Plain answers take the
 * Markdown path unchanged, chunking and off-screen unmounting included. */
export default memo(function Answer({ text, streaming = false, sources, virtual, onOpenLink, runId, uiState, saveState }: Props) {
  const parts = useMemo(() => (text.includes("```openui") ? splitAnswerParts(text, streaming) : null), [text, streaming]);
  const states = useMemo<BlockStates | null>(() => {
    if (!runId) return null;
    return {
      initial: (i) => startingState(runId, uiState)?.blocks[String(i)],
      save: saveState ? (i, snapshot) => saveBlock(runId, i, snapshot, startingState(runId, uiState)) : undefined,
    };
  }, [runId, uiState, saveState]);
  if (!parts) return <Markdown virtual={virtual} text={text} streaming={streaming} sources={sources} onOpenLink={onOpenLink} />;
  let blocks = 0;
  return (
    <BlockStatesProvider value={states}>
      <div className="answer-parts">
        {parts.map((part, i) => {
          const last = i === parts.length - 1;
          if (part.kind === "md") return part.text.trim() ? <Markdown key={part.index} virtual={virtual} text={part.text} streaming={streaming && last} sources={sources} onOpenLink={onOpenLink} /> : null;
          // past the limit a block stays source: bounded rendering cost per answer
          if (++blocks > MAX_BLOCKS) return <Markdown key={part.index} text={"```openui\n" + part.text.trimEnd() + "\n```"} />;
          return <GenUIBlock key={part.index} index={blocks - 1} source={part.text} streaming={streaming && last} closed={part.closed} />;
        })}
      </div>
    </BlockStatesProvider>
  );
});
