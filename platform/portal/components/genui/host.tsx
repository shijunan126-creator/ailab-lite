"use client";

import { createContext, useContext } from "react";

/** What a page offers generated UI: whether it may act, and how a button's message is sent (HLD §6.8). */
export type GenUIHost = {
  readOnly: boolean;
  /** Sends a message as the user, through the same path as the composer (queue/steer rules included). */
  send?: (text: string) => Promise<boolean>;
};

const Host = createContext<GenUIHost>({ readOnly: true });
export const GenUIHostProvider = Host.Provider;
export const useGenUIHost = () => useContext(Host);

/** Where one answer's blocks start from and how a change is kept (lib/ui-state). Without it state is memory only;
 * without `save` (share pages) the reader can still use the controls, nothing is written. */
export type BlockStates = {
  initial: (index: number) => Record<string, unknown> | undefined;
  save?: (index: number, snapshot: Record<string, unknown>) => void;
};

const States = createContext<BlockStates | null>(null);
export const BlockStatesProvider = States.Provider;
export const useBlockStates = () => useContext(States);

/** Where a block's fence stands: still being written, closed (only then may a generated page run), or cut off (the
 * answer ended before the fence closed, so the block will never be complete). */
export type BlockPhase = "writing" | "complete" | "cut";
const Phase = createContext<BlockPhase>("writing");
export const BlockPhaseProvider = Phase.Provider;
export const useBlockPhase = () => useContext(Phase);
