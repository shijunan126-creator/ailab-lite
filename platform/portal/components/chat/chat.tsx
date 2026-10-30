"use client";

import type { SkillPick } from "../../lib/message-parts";
import { useCallback, useEffect, useMemo, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Conversation, ConversationDetail, downloadUrl, errorCode, errorMessage, EventItem, filesByIds, MAX_ATTACHMENTS, ModelInfo, Preview, Project, QueuedMessage, Run, streamRun } from "../../lib/api";
import { createBatcher } from "../../lib/stream/batcher";
import { useFollow } from "../../lib/use-follow";
import { handleClientCall, type ClientCall } from "../../lib/js/client-calls";
import { extractSkill } from "../../lib/skill-drafts";
import { useStable } from "../../lib/use-stable";
import { cacheConversation, cachedConversation } from "../../lib/conversation-cache";
import { mergeTurns, mergePreviews, refreshConversation } from "../../lib/conversation-pages";
import { isTerminal } from "../../lib/format";
import { projectsChanged, useProjects } from "../../lib/projects";
import { conversationsChanged, conversationTitled } from "../app-shell";
import { CONVERSATION_MOVED } from "../conversation-menu";
import { IconArrowDown, IconBranch, IconChevronRight, IconFolder, IconPanelRight } from "../icons";
import ProjectHome from "../project-home";
import Composer, { Attachment, Outgoing, SendOptions } from "./composer";
import PendingMessage from "./pending-message";
import { enqueue, remove as removePending, useOutbox, type Pending } from "../../lib/outbox";
import { useShallow } from "zustand/react/shallow";
import ModeToggle, { useChatMode } from "./mode-toggle";
import PreviewPane, { previewTabKey, type PanePreview, type PaneSources } from "./preview-pane";
import { SourcesSheet } from "./sources";
import type { PaneReader } from "./preview-pane";
import QueueStrip from "./queue-strip";
import QuotaBanner from "./quota-banner";
import Turn, { ForkMode, Item, reduceEvent } from "./turn";
import { GenUIHostProvider, type GenUIHost } from "../genui/host";

// Shared, not rebuilt per render: Turn is memoised and a fresh array would re-render every turn on any refresh.
const CHAT_OR_WORK: ForkMode[] = ["chat", "work"];
const WORK_ONLY: ForkMode[] = ["work"];

function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModelState] = useState("");
  useEffect(() => {
    // The platform decides which models exist; the user only picks one (their last choice is remembered).
    api<{ default: string | null; models: ModelInfo[] }>("/v1/models")
      .then((res) => {
        setModels(res.models);
        const remembered = localStorage.getItem("ailab.model");
        setModelState(res.models.some((m) => m.id === remembered) ? remembered! : res.default || res.models[0]?.id || "");
      })
      .catch(() => setModels([]));
  }, []);
  const setModel = (id: string) => {
    setModelState(id);
    localStorage.setItem("ailab.model", id);
  };
  return { models, model, setModel };
}

/** "Talk about these": the library opens a new chat with ?files=a,b already attached. */
function useInitialFiles(active: boolean): Attachment[] | undefined {
  const [files, setFiles] = useState<Attachment[]>();
  useEffect(() => {
    if (!active) return;
    const url = new URL(window.location.href);
    const ids = (url.searchParams.get("files") || "").split(",").filter(Boolean).slice(0, MAX_ATTACHMENTS);
    if (!ids.length) return;
    url.searchParams.delete("files");
    window.history.replaceState(null, "", url.pathname + url.search); // a reload must not attach them again
    filesByIds(ids)
      .then((list) => setFiles(list.filter((f) => f.status === "ready")))
      .catch(() => undefined);
  }, [active]);
  return files;
}

export default function Chat({ id, project }: { id: string | null; project?: Project | null }) {
  const [mode, setMode] = useChatMode();
  const router = useRouter();
  const initialFiles = useInitialFiles(!id);
  const projects = useProjects();
  const { models, model, setModel } = useModels();
  // a conversation opened before in this tab shows at once; the fresh copy replaces it when it arrives
  const [detail, setDetail] = useState<ConversationDetail | null>(() => (id ? cachedConversation(id) : null));
  const modeInitialized = useRef(false);
  function chooseMode(next: "chat" | "work") {
    modeInitialized.current = true;
    setMode(next);
  }
  useEffect(() => {
    if (!detail || modeInitialized.current) return;
    modeInitialized.current = true;
    // Copied turns retain their original mode; the fork choice governs until its first new turn.
    const ownTurn = [...detail.turns].reverse().find((turn) => !turn.result?.forked_from_run_id);
    const saved = ownTurn?.options?.mode || detail.forked_from?.mode || detail.turns.at(-1)?.options?.mode;
    if (saved === "chat" || saved === "work") setMode(saved);
  }, [detail, setMode]);
  const [items, setItems] = useState<Record<string, Item[]>>({}); // run id -> live items (runs streamed in this page)
  const [livePreviews, setLivePreviews] = useState<Preview[]>([]);
  const [libraryPreviews, setLibraryPreviews] = useState<PanePreview[]>([]);
  const [paneOpen, setPaneOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [closedFiles, setClosedFiles] = useState<string[]>([]); // preview tabs closed one by one
  const [paneSources, setPaneSources] = useState<PaneSources | null>(null); // a turn's sources: a pane tab on wide screens
  const [sourcesOn, setSourcesOn] = useState(false); // that tab is the one shown
  const [readers, setReaders] = useState<PaneReader[]>([]);
  const [readerUrl, setReaderUrl] = useState<string | null>(null);
  const paneReader = readers.find((r) => r.url === readerUrl) || readers[readers.length - 1];
  const [readerOn, setReaderOn] = useState(false);
  const [sheetSources, setSheetSources] = useState<PaneSources | null>(null); // the same on a phone, as a bottom sheet
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  // messages sent from this page that are not turns yet (files still uploading, or a failed send)
  const pendingProject = id ? null : project?.id || null;
  const pending = useOutbox(useShallow((st) => Object.values(st.pending).filter((p) => p.conversationId === id && (id || p.projectId === pendingProject)).sort((a, b) => a.created - b.created)));
  const scroller = useRef<HTMLDivElement | null>(null);
  const tail = useFollow(scroller, { slack: 80 });
  const stick = tail.following;
  // the reader went up into the history: offer the way back, never pull them down
  const [away, setAway] = useState(false);
  useEffect(() => setAway(false), [id]);
  const loadSeq = useRef(0);
  const detailController = useRef<AbortController | null>(null);
  const olderController = useRef<AbortController | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const prependHeight = useRef<number | null>(null);
  const actionController = useRef(new AbortController());
  useEffect(() => {
    actionController.current = new AbortController();
    return () => { actionController.current.abort(); streamController.current?.abort(); };
  }, []);
  const streamController = useRef<AbortController | null>(null);
  const eventCursors = useRef<Record<string, number>>({});
  const updatedPreview = useRef<Record<string, string>>({}); // run id -> the last file it re-rendered while followed here
  useEffect(() => {
    if (detail) cacheConversation(detail);
  }, [detail]);
  useEffect(() => {
    if (!id) return;
    document.title = detail?.title ? `${detail.title} · AILab` : "AILab";
  }, [id, detail?.title]);
  useEffect(() => () => { document.title = "AILab"; }, []);

  // A read started before a confirmed write can still contain the old queue or turn.
  const detailWriteVersion = useRef(0);
  function markDetailWrite() {
    ++detailWriteVersion.current;
  }

  const loadDetail = useCallback(async (): Promise<ConversationDetail | null> => {
    if (!id) return null;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const seq = ++loadSeq.current;
    const version = detailWriteVersion.current;
    try {
      const d = await api<ConversationDetail>(`/v1/conversations/${id}?limit=20`, { signal: controller.signal });
      if (seq !== loadSeq.current) return null;
      // Keep the caller's refresh/follow continuation, but obtain a snapshot after the write.
      if (version !== detailWriteVersion.current) return loadDetail();
      setDetail((previous) => refreshConversation(previous, d));
      return d;
    } catch (err: any) {
      if (!controller.signal.aborted && seq === loadSeq.current) setError(errorMessage(err));
      return null;
    }
  }, [id]);

  // A status or artifact change mid-run refreshes that one turn, coalesced; the whole page reloads when the run ends.
  const runRefresh = useRef<{ timer: ReturnType<typeof setTimeout> | null; controller: AbortController | null }>({ timer: null, controller: null });
  const refreshRun = useCallback((runId: string) => {
    const pending = runRefresh.current;
    if (pending.timer) return;
    pending.timer = setTimeout(async () => {
      pending.timer = null;
      pending.controller?.abort();
      const controller = new AbortController();
      pending.controller = controller;
      const version = detailWriteVersion.current;
      try {
        const fresh = await api<Run>(`/v1/runs/${runId}`, { signal: controller.signal });
        if (controller.signal.aborted || version !== detailWriteVersion.current) return;
        // fields only the conversation view carries (versions, steered) stay as they were
        setDetail((d) => (d && d.turns.some((t) => t.id === runId) ? { ...d, turns: d.turns.map((t) => (t.id === runId ? { ...t, ...fresh, versions: fresh.versions ?? t.versions, steered: fresh.steered ?? t.steered } : t)) } : d));
      } catch {
        // the end-of-run reload brings the same state
      }
    }, 300);
  }, []);
  useEffect(() => () => {
    if (runRefresh.current.timer) clearTimeout(runRefresh.current.timer);
    runRefresh.current.controller?.abort();
  }, []);

  const follow = useCallback(
    (run: Run) => {
      if (actionController.current.signal.aborted) return new AbortController();
      streamController.current?.abort();
      const controller = new AbortController();
      streamController.current = controller;
      setStreaming(run.id);
      setItems((prev) => ({ [run.id]: prev[run.id] || [] }));
      // every event of a frame folds into the items in one update
      const batch = createBatcher<EventItem>((events) => {
        if (controller.signal.aborted) return;
        setItems((prev) => ({ ...prev, [run.id]: events.reduce(reduceEvent, prev[run.id] || []) }));
      });
      controller.signal.addEventListener("abort", () => batch.cancel(), { once: true });
      streamRun(
        run.id,
        (event) => {
          if (controller.signal.aborted) return;
          if (event.seq) eventCursors.current[run.id] = event.seq;
          batch.push(event);
          if (event.type === "preview.updated") {
            const p: Preview = { ...event.payload, run_id: run.id };
            updatedPreview.current[run.id] = p.filename;
            setLivePreviews((prev) => [...prev.filter((x) => x.filename !== p.filename), p]);
          }
          if (event.type === "conversation.titled" && event.payload?.title) {
            const title = String(event.payload.title);
            setDetail((d) => (d && !d.title ? { ...d, title } : d));
            conversationTitled(String(event.payload.conversation_id || run.conversation_id || id || ""), title);
          }
          if (event.type === "run.status" || event.type === "artifact.created") refreshRun(run.id);
          // the model asked this page to run a small program: claim it, run it in a worker, report back
          if (event.type === "client.call" && event.payload?.call_id) void handleClientCall(run.id, event.payload as ClientCall);
          if (["run.completed", "run.failed", "run.canceled"].includes(event.type)) {
            batch.flush();
            loadDetail();
          }
        },
        controller.signal,
        () => {
          if (controller.signal.aborted) return;
          batch.flush();
          setStreaming(null);
          loadDetail().then((d) => {
            // a file made or changed by this turn opens once the turn is done, not on every intermediate render
            const file = updatedPreview.current[run.id];
            delete updatedPreview.current[run.id];
            // a message queued behind this answer has started on its own: follow it like one sent from here
            const next = d?.turns[d.turns.length - 1];
            if (next && next.id !== run.id && !isTerminal(next.status, !!next.result?.file_wait) && !controller.signal.aborted) {
              stick.current = true;
              follow(next);
            }
            const finished = d?.turns.find((t) => t.id === run.id);
            if (!file || finished?.status !== "succeeded" || controller.signal.aborted) return;
            if (!window.matchMedia("(min-width: 761px)").matches) return; // on a phone the pane covers the chat: the file card opens it
            showFileRef.current(file);
          });
          conversationsChanged();
        },
        eventCursors.current[run.id] || 0
      ).catch((err) => {
        if (!controller.signal.aborted) {
          batch.flush();
          setStreaming(null);
          setError(errorMessage(err));
          loadDetail();
        }
      });
      return controller;
    },
    [loadDetail, refreshRun]
  );

  // open a conversation: load it, and follow its last turn if that is still being answered
  useEffect(() => {
    if (!id) return;
    let disposed = false;
    let controller: AbortController | null = null;
    // a search result names the turn it found: show it, on its own version of the conversation if need be
    const url = new URL(window.location.href);
    const target = url.searchParams.get("run");
    if (target) {
      url.searchParams.delete("run");
      window.history.replaceState(window.history.state, "", url.pathname + url.search);
    }
    loadDetail().then(async (d) => {
      if (disposed) return;
      const last = d?.turns[d.turns.length - 1];
      if (last && (!isTerminal(last.status, !!last.result?.file_wait) || last.status === "interrupted")) controller = follow(last);
      else if (target && d && !d.turns.some((t) => t.id === target)) await switchRef.current(target);
      if (target && !disposed) requestAnimationFrame(() => reveal(target));
    });
    return () => {
      disposed = true;
      ++loadSeq.current;
      detailController.current?.abort();
      olderController.current?.abort();
      controller?.abort();
      streamController.current?.abort();
    };
  }, [id, loadDetail, follow]);

  async function loadOlder() {
    if (!id || !detail?.has_more || !detail.turns.length || olderController.current) return;
    const controller = new AbortController();
    olderController.current = controller;
    setLoadingOlder(true);
    try {
      const page = await api<ConversationDetail>(`/v1/conversations/${id}?limit=20&before=${encodeURIComponent(detail.turns[0].id)}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const el = scroller.current;
      prependHeight.current = el ? el.scrollHeight - el.scrollTop : null;
      stick.current = false;
      for (const t of page.turns) opened.current?.add(t.id);
      setDetail((d) => d && d.id === page.id ? { ...d, turns: mergeTurns(page.turns, d.turns),
        previews: mergePreviews(page.previews, d.previews), has_more: page.has_more } : d);
    } catch (err: any) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (olderController.current === controller) olderController.current = null;
      if (!controller.signal.aborted) setLoadingOlder(false);
    }
  }

  useEffect(() => {
    const moved = (e: Event) => {
      const { id: moved, project_id } = (e as CustomEvent).detail || {};
      if (moved === id) setDetail((d) => (d ? { ...d, project_id } : d));
    };
    window.addEventListener(CONVERSATION_MOVED, moved);
    return () => window.removeEventListener(CONVERSATION_MOVED, moved);
  }, [id]);

  // keep the newest message in view while it streams, unless the user scrolled up to read
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && prependHeight.current !== null) {
      el.scrollTop = el.scrollHeight - prependHeight.current;
      prependHeight.current = null;
    } else tail.settle();
  }, [items, detail, pending.length, tail]);

  const previews = useMemo<PanePreview[]>(() => {
    const byFile = new Map<string, Preview>();
    for (const p of [...(detail?.previews || []), ...livePreviews]) {
      byFile.delete(p.filename);
      byFile.set(p.filename, p);
    }
    // Skills deliver self-contained HTML decks: they preview in the pane straight from the
    // delivered file, newest delivery wins. `version` counts deliveries of this name so the
    // pane's iframe refetches (and the viewer shows it) on every regeneration.
    const htmlVersions = new Map<string, number>();
    for (const t of detail?.turns || []) {
      for (const a of t.artifacts || []) {
        if ((a.content_type || "").split(";")[0] !== "text/html" && !/\.(html?|xhtml)$/i.test(a.filename)) continue;
        const version = (htmlVersions.get(a.filename) || 0) + 1;
        htmlVersions.set(a.filename, version);
        byFile.set(a.filename, { filename: a.filename, version, run_id: t.id, kind: "html", file_id: a.file_id });
      }
    }
    return [...[...byFile.values()].filter((p) => p.kind), ...libraryPreviews];
  }, [detail, livePreviews, libraryPreviews]);

  // A newly delivered (or regenerated) HTML deck opens in the pane by itself, so the next
  // prompt can be written while looking at the result. The first pass over a loaded conversation
  // only takes note: opening old deliveries on load would be noise. "First" means the first time
  // the conversation's detail exists, not the first time there is an HTML file — a conversation's
  // very first deck is a delivery, not history.
  const seenHtml = useRef<Map<string, string>>(new Map());
  const htmlBaselined = useRef(false);
  const hasDetail = !!detail;
  const noted = useMemo(() => previews.filter((p) => p.kind === "html"), [previews]);
  useEffect(() => {
    if (!hasDetail) return;
    if (!htmlBaselined.current) {
      htmlBaselined.current = true;
      for (const p of noted) seenHtml.current.set(p.filename, p.file_id || "");
      return;
    }
    const fresh = noted.filter((p) => seenHtml.current.get(p.filename) !== p.file_id);
    for (const p of fresh) seenHtml.current.set(p.filename, p.file_id || "");
    if (fresh.length) {
      const last = fresh[fresh.length - 1];
      setClosedFiles((closed) => closed.filter((f) => f !== last.filename));
      setSelected(last.filename);
      setSourcesOn(false);
      setReaderOn(false);
      setPaneOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noted, hasDetail]);

  const downloads = useMemo(() => {
    const out: Record<string, string> = {};
    for (const t of detail?.turns || []) for (const a of t.artifacts || []) out[a.filename] = downloadUrl(a.download_url);
    for (const p of libraryPreviews) out[previewTabKey(p)] = downloadUrl(`/v1/files/${p.libraryFileId}/content`);
    return out;
  }, [detail, libraryPreviews]);

  const modelName = (m: string) => models.find((x) => x.id === m)?.name || m;

  const openFiles = previews.filter((p) => !closedFiles.includes(previewTabKey(p)));
  function showFile(filename: string) {
    setClosedFiles((closed) => closed.filter((f) => f !== filename));
    setSelected(filename);
    setSourcesOn(false);
    setReaderOn(false);
    setPaneOpen(true);
  }
  function showLibraryFile(file: { file_id: string; filename: string }) {
    const preview = { filename: file.filename, version: 1, libraryFileId: file.file_id };
    setLibraryPreviews((previous) => previous.some((p) => p.libraryFileId === file.file_id) ? previous : [...previous, preview]);
    showFile(previewTabKey(preview));
  }
  function closeFile(filename: string) {
    const rest = openFiles.filter((p) => previewTabKey(p) !== filename);
    setClosedFiles((closed) => [...closed, filename]);
    if (!rest.length && !paneSources && !readers.length) return setPaneOpen(false);
    if (!rest.length) {
      if (!readerOn) {
        if (paneSources) setSourcesOn(true);
        else setReaderOn(true);
      }
      return;
    }
    if (selected === filename || !rest.some((p) => previewTabKey(p) === selected)) setSelected(previewTabKey(rest[rest.length - 1]));
  }
  function showSources(run: Run, sources: PaneSources["sources"], answer: string) {
    const next = { runId: run.id, sources, answer };
    if (!window.matchMedia("(min-width: 761px)").matches) return setSheetSources(next);
    // the same turn's sources, already shown: the button closes them again
    if (paneOpen && sourcesOn && paneSources?.runId === run.id) return closeSources();
    setPaneSources(next);
    setSourcesOn(true);
    setReaderOn(false);
    setPaneOpen(true);
  }
  function closeSources() {
    setPaneSources(null);
    setSourcesOn(false);
    if (!openFiles.length) {
      if (readers.length) setReaderOn(true);
      else setPaneOpen(false);
    }
  }
  // Stable identity: it is handed to memoized Markdown as onOpenLink, and a fresh literal would
  // re-render every answered turn on each stream event.
  const openReader = useCallback((source: { url: string; title: string }) => {
    let url: URL;
    try { url = new URL(source.url); } catch { return; }
    if (!["http:", "https:"].includes(url.protocol)) return;
    const next = { url: url.href, title: source.title || url.hostname };
    setReaders((prev) => prev.some((r) => r.url === next.url) ? prev : [...prev, next]);
    setReaderUrl(next.url);
    setReaderOn(true);
    setSourcesOn(false);
    setPaneOpen(true);
  }, []);
  function closeReader(url: string) {
    const rest = readers.filter((r) => r.url !== url);
    setReaders(rest);
    if (paneReader?.url !== url) return;
    setReaderUrl(rest[rest.length - 1]?.url || null);
    if (rest.length) return;
    setReaderOn(false);
    if (paneSources) setSourcesOn(true);
    else if (!openFiles.length) setPaneOpen(false);
  }
  const ext = (name: string) => name.split(".").pop()?.toLowerCase() || "";
  // Previews are keyed by the file's name in the sandbox; deliver_file may have given it another name.
  // Then the one preview of the same kind that this turn rendered is the delivered file's.
  const previewKey = (run: Run, filename: string): string | null => {
    if (previews.some((p) => !p.libraryFileId && p.filename === filename)) return filename;
    const own = (run.result?.previews || []).filter((p) => ext(p.filename) === ext(filename));
    return own.length === 1 && previews.some((p) => !p.libraryFileId && p.filename === own[0].filename) ? own[0].filename : null;
  };
  const turns = detail?.turns || [];
  const handledSends = useRef(new Set<string>());
  useEffect(() => {
    for (const p of pending) {
      if (p.stage !== "sent" || handledSends.current.has(p.id)) continue;
      handledSends.current.add(p.id);
      takeSent(p);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  /** A message the outbox has sent: its run (or queue entry) takes the place of the pending message. */
  function takeSent(p: Pending) {
    const { run, queued, conversation } = p.sent || {};
    removePending(p.id);
    if (queued) {
      markDetailWrite();
      setDetail((d) => (d ? { ...d, queue: [...(d.queue || []).filter((q) => q.id !== queued.id), queued] } : d));
      return;
    }
    if (!run) return;
    if (!id && conversation) {
      // the conversation page starts from what is already known: the question shows at once and its answer streams in
      cacheConversation({ ...conversation, turns: [run], previews: [], has_more: false });
      conversationsChanged();
      if (p.projectId) projectsChanged();
      // the blank new-chat page becomes this conversation, so Back leaves it rather than returning to an empty composer;
      // a project's page is a page of its own and stays behind it
      if (p.projectId) router.push(`/c/${conversation.id}`);
      else router.replace(`/c/${conversation.id}`);
      return;
    }
    markDetailWrite();
    setDetail((d) => (d && !d.turns.some((t) => t.id === run.id) ? { ...d, turns: [...d.turns, run] } : d));
    stick.current = true;
    follow(run);
    conversationsChanged();
  }
  // turns already there when the conversation opened appear with it; only turns that arrive afterwards animate in
  const opened = useRef<Set<string> | null>(null);
  if (detail && opened.current === null) opened.current = new Set(detail.turns.map((t) => t.id));
  // a conversation that has worked in files can only branch into work: chat has no workspace to carry on in.
  // A work branch is work from its first turn, including the turns it carried over.
  const forkedAsWork = detail?.forked_from?.mode === "work";
  const forkModes = useMemo(() => {
    let worked = forkedAsWork;
    return turns.map((t) => {
      worked ||= t.options?.mode !== "chat";
      return worked ? WORK_ONLY : CHAT_OR_WORK;
    });
  }, [turns, forkedAsWork]);
  const projectId = id ? detail?.project_id || null : project?.id || null;
  const crumb = id && projectId ? projects?.find((p) => p.id === projectId) : null;
  const running = turns.some((t) => !isTerminal(t.status, !!t.result?.file_wait));
  // a question the agent is waiting on: the composer answers it instead of starting a new turn
  const waiting = turns.find((t) => t.status === "waiting_user" || t.status === "interrupted");
  const question = waiting
    ? ([...(items[waiting.id] || [])].reverse().find((it) => it.kind === "ask" && it.answer === undefined) as Extract<Item, { kind: "ask" }> | undefined)
    : undefined;

  async function send(prompt: string, fileIds: string[], options: SendOptions = { mode: "work" }): Promise<boolean> {
    setBusy(true);
    setError("");
    try {
      const signal = actionController.current.signal;
      if (waiting && question) {
        await answer(waiting, question.id, prompt);
        return true;
      }
      let created: Conversation | null = null;
      if (!id)
        created = await api<Conversation>("/v1/conversations", { method: "POST", signal, body: JSON.stringify({ model, project_id: projectId || undefined }) });
      const convId = id || created!.id;
      const run = await api<Run>(`/v1/conversations/${convId}/messages`, {
        method: "POST", signal,
        body: JSON.stringify({ prompt, model: model || undefined, file_ids: fileIds, ...options }),
      });
      if (created) {
        // the conversation page starts from what is already known: the question shows at once and its answer streams in
        cacheConversation({ ...created, turns: [run], previews: [], has_more: false });
        conversationsChanged();
        if (projectId) projectsChanged();
        // the blank new-chat page becomes this conversation, so Back leaves it rather than returning to an empty composer;
        // a project's page is a page of its own and stays behind it
        if (projectId) router.push(`/c/${convId}`);
        else router.replace(`/c/${convId}`);
        return true;
      }
      markDetailWrite();
      setDetail((d) => (d ? { ...d, turns: [...d.turns, run] } : d));
      stick.current = true;
      follow(run);
      conversationsChanged();
      return true;
    } catch (err: any) {
      // the server's conversation_busy message is already the friendly, actionable text
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function queue(prompt: string, fileIds: string[], options: SendOptions, steer: boolean): Promise<boolean> {
    if (!id) return false;
    setBusy(true);
    setError("");
    try {
      const item = await api<QueuedMessage>(`/v1/conversations/${id}/queue`, {
        method: "POST",
        signal: actionController.current.signal,
        body: JSON.stringify({ prompt, file_ids: fileIds, model: model || undefined, steer, ...options }),
      });
      markDetailWrite();
      setDetail((d) => (d ? { ...d, queue: [...(d.queue || []).filter((q) => q.id !== item.id), item] } : d));
      return true;
    } catch (err) {
      const message = errorMessage(err);
      // the answer finished while this was being written: it goes out as a normal message
      if (errorCode(err) === "conversation_idle") {
        await loadDetail();
        return send(prompt, fileIds, options);
      }
      setError(message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function changeQueued(qid: string, body: { prompt?: string; steer?: boolean } | null): Promise<boolean> {
    if (!id) return false;
    try {
      if (body) {
        const item = await api<QueuedMessage>(`/v1/conversations/${id}/queue/${qid}`, { method: "PATCH", body: JSON.stringify(body) });
        markDetailWrite();
        setDetail((d) => (d ? { ...d, queue: (d.queue || []).map((q) => (q.id === qid ? item : q)) } : d));
      } else {
        await api(`/v1/conversations/${id}/queue/${qid}`, { method: "DELETE" });
        markDetailWrite();
        setDetail((d) => (d ? { ...d, queue: (d.queue || []).filter((q) => q.id !== qid) } : d));
      }
      return true;
    } catch (err) {
      setError(errorMessage(err));
      loadDetail();
      return false;
    }
  }

  async function resumeQueue() {
    if (!id) return;
    try {
      await api<QueuedMessage>(`/v1/conversations/${id}/queue/resume`, { method: "POST" });
      const d = await loadDetail();
      const started = d?.turns[d.turns.length - 1];
      if (started && !isTerminal(started.status, !!started.result?.file_wait)) {
        stick.current = true;
        follow(started);
      }
    } catch (err) {
      setError(errorMessage(err));
      loadDetail();
    }
  }

  async function answer(run: Run, questionId: string, value: string) {
    const resumed = await api<Run>(`/v1/runs/${run.id}/answer`, {
      method: "POST", signal: actionController.current.signal,
      body: JSON.stringify({ question_id: questionId, answer: value }),
    });
    markDetailWrite();
    setDetail((d) => (d ? { ...d, turns: d.turns.map((t) => (t.id === run.id ? resumed : t)) } : d));
    follow(resumed);
  }

  async function resume(run: Run) {
    setError("");
    try {
      const resumed = await api<Run>(`/v1/runs/${run.id}/resume`, { method: "POST", signal: actionController.current.signal });
      markDetailWrite();
      setDetail((d) => (d ? { ...d, turns: d.turns.map((t) => (t.id === run.id ? resumed : t)) } : d));
      stick.current = true;
      follow(resumed);
    } catch (err: any) {
      setError(errorMessage(err));
    }
  }

  /** A new version of a turn (regenerated, or its message edited): it and everything after it give way to the new one. */
  function branchTo(old: Run, fresh: Run) {
    olderController.current?.abort();
    olderController.current = null;
    setLoadingOlder(false);
    setLivePreviews([]);
    setLibraryPreviews([]);
    setDetail((d) => {
      if (!d) return d;
      const at = d.turns.findIndex((t) => t.id === old.id);
      return { ...d, turns: [...(at < 0 ? d.turns : d.turns.slice(0, at)).filter((t) => t.id !== fresh.id), fresh] };
    });
    stick.current = true;
    follow(fresh);
    loadDetail(); // the run alone does not carry its versions: the conversation does
    conversationsChanged();
  }

  /** Answer the last message again; the old answer stays as another version. */
  async function retry(run: Run) {
    setError("");
    try {
      const fresh = await api<Run>(`/v1/runs/${run.id}/retry`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ model: model || null }),
      });
      branchTo(run, fresh);
    } catch (err: any) {
      if (!actionController.current.signal.aborted) setError(errorMessage(err));
    }
  }

  /** Send an earlier message again, changed; the conversation continues from there. */
  async function edit(run: Run, prompt: string, skills: SkillPick[]): Promise<boolean> {
    setError("");
    try {
      const fresh = await api<Run>(`/v1/runs/${run.id}/edit`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ prompt, skills, file_ids: null, model: model || null, mode: null }),
      });
      branchTo(run, fresh);
      return true;
    } catch (err: any) {
      if (!actionController.current.signal.aborted) setError(errorMessage(err));
      return false;
    }
  }

  const switchRef = useRef(switchTo);
  switchRef.current = switchTo;

  /** Show another version of a turn: the conversation below it follows that version. */
  async function switchTo(runId: string) {
    if (!id) return;
    setError("");
    olderController.current?.abort();
    olderController.current = null;
    setLoadingOlder(false);
    try {
      const page = await api<ConversationDetail>(`/v1/conversations/${id}/active`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ run_id: runId }),
      });
      for (const t of page.turns) opened.current?.add(t.id); // a version switch is not new content arriving
      ++loadSeq.current; // a detail load already in flight would bring the old path back
      olderController.current?.abort();
      olderController.current = null;
      setLoadingOlder(false);
      setLivePreviews([]);
      setLibraryPreviews([]);
      setDetail((d) => (d && d.id === page.id ? { ...page, turns: page.turns } : page));
    } catch (err: any) {
      if (!actionController.current.signal.aborted) setError(errorMessage(err));
    }
  }

  /** Continue from a turn in a new conversation; what came after it stays where it was. */
  async function fork(run: Run, mode: ForkMode) {
    if (!id) return;
    setError("");
    try {
      const branch = await api<Conversation>(`/v1/conversations/${id}/fork`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ run_id: run.id, mode }),
      });
      localStorage.setItem("ailab.mode", mode);
      conversationsChanged();
      if (branch.project_id) projectsChanged();
      router.push(`/c/${branch.id}`);
    } catch (err: any) {
      if (!actionController.current.signal.aborted) setError(errorMessage(err));
    }
  }

  async function stop() {
    const active = turns.find((t) => !isTerminal(t.status, !!t.result?.file_wait));
    if (active) await api(`/v1/runs/${active.id}/cancel`, { method: "POST" }).catch(() => undefined);
    loadDetail();
  }

  // Stable identities for what every turn receives: a stream event re-renders only the turn it belongs to.
  const showFileRef = useRef(showFile);
  showFileRef.current = showFile;
  const turnPreview = useStable(showFile);
  const turnPreviewFile = useStable(showLibraryFile);
  const turnPreviewKey = useStable(previewKey);
  const turnResume = useStable(resume);
  const turnAnswer = useStable(answer);
  const turnRetry = useStable(retry);
  const turnExtract = useStable(async () => {
    if (!id) return false;
    setError("");
    try {
      await extractSkill(id);
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    }
  });
  const turnEdit = useStable(edit);
  const turnSwitch = useStable(switchTo);
  const turnFork = useStable(fork);
  const turnSources = useStable(showSources);
  const turnOpenLink = useStable((url: string, title: string) => openReader({ url, title }));

  /** The composer's message: answers a waiting question directly; anything else goes out through the outbox, which
   * shows it at once and asks the model only when its files are ready. */
  async function submit(message: Outgoing): Promise<boolean> {
    if (waiting && question && !message.attachments.length) return send(message.prompt, [], message.options);
    const info = models.find((m) => m.id === model);
    enqueue({
      conversationId: id, projectId: projectId, prompt: message.prompt, attachments: message.attachments, options: message.options,
      model, vision: !!info?.capabilities?.includes("vision"), steer: message.steer, queued: running && !!id && !question,
    });
    stick.current = true;
    return true;
  }

  // a generated button sends like the composer: as a new message, or queued behind the answer that is running
  const genuiSend = useStable((text: string) => submit({ prompt: text, attachments: [], options: { mode }, steer: false }));
  const genuiHost = useMemo<GenUIHost>(() => ({ readOnly: false, send: genuiSend }), [genuiSend]);

  const showPane = paneOpen && (openFiles.length > 0 || !!paneSources || !!paneReader);
  const reopenCount = !showPane ? previews.length + (paneSources ? 1 : 0) + readers.length : !openFiles.length ? previews.length : 0;
  const composer = (
    <>
    <QuotaBanner refresh={streaming} />
    {/* a project's new conversation picks its mode here; the home page shows the choice under its heading */}
    {!id && project ? <div className="composer-mode"><ModeToggle mode={mode} onChange={chooseMode} /></div> : null}
    <Composer
      mode={mode}
      onMode={chooseMode}
      modeLocked={!!id}
      models={models}
      model={model}
      onModel={setModel}
      busy={busy}
      running={running && !question}
      queueable={!!id && !question}
      onSubmit={submit}
      onStop={stop}
      autoFocus
      placeholder={question ? "回答上面的问题" : running ? "补充或下一条消息" : undefined}
      projectId={projectId}
      initialFiles={initialFiles}
      context={detail?.context ? { used: detail.context.used_tokens, compactAt: detail.context.compact_at_tokens } : undefined}
    />
    </>
  );

  return (
    <div className={`chat-layout ${showPane ? "with-preview" : ""}`}>
      <section className="chat-main">
        {!id && project && !pending.length ? (
          <ProjectHome project={project} composer={composer} error={error} />
        ) : !id && pending.length ? (
          <>
            <div className="messages">
              <div className="messages-inner">{pending.map((p) => <PendingMessage key={p.id} message={p} />)}</div>
            </div>
            <div className="composer-dock">
              {error ? <div className="alert error">{error}</div> : null}
              {composer}
            </div>
          </>
        ) : !id ? (
          <>
            <div className="chat-home">
              <div className="home-hero">
                <h1>有什么我能帮你的吗？</h1>
                <ModeToggle mode={mode} onChange={chooseMode} />
              </div>
            </div>
            <div className="composer-dock">
              {error ? <div className="alert error">{error}</div> : null}
              {composer}
            </div>
          </>
        ) : (
          <>
            {reopenCount ? (
              <button className="icon-btn pane-reopen" aria-label={`打开预览（${reopenCount}）`} title="展开预览" onClick={() => {
                setClosedFiles([]);
                // a collapsed pane comes back as it was; an open one without files switches to them
                if (showPane || (!paneSources && !readers.length)) {
                  setSelected((value) => value || (previews.length ? previewTabKey(previews[previews.length - 1]) : null));
                  setSourcesOn(false);
                  setReaderOn(false);
                }
                setPaneOpen(true);
              }}>
                <IconPanelRight size={18} />
                <span className="count">{reopenCount}</span>
              </button>
            ) : null}
            <div
              className="messages"
              ref={scroller}
              onScroll={(e) => {
                tail.onScroll(e);
                const el = e.currentTarget;
                setAway(el.scrollHeight - el.scrollTop - el.clientHeight > Math.max(160, el.clientHeight * 0.35));
                if (el.scrollTop < 80) void loadOlder();
              }}
            >
              <GenUIHostProvider value={genuiHost}>
              <div className="messages-inner" ref={tail.content}>
                {crumb || detail?.forked_from ? (
                  <div className="chat-crumb">
                    {crumb ? (
                      <>
                        <Link href={`/p/${crumb.id}`}>
                          <IconFolder size={15} /> {crumb.name}
                        </Link>
                        <IconChevronRight size={14} />
                      </>
                    ) : null}
                    <span className="crumb-title">{detail?.title || "新对话"}</span>
                    {detail?.forked_from ? (
                      <Link className="crumb-fork" href={`/c/${detail.forked_from.conversation_id}`} title="打开原对话">
                        <IconBranch size={13} /> 分支
                      </Link>
                    ) : null}
                  </div>
                ) : null}
                {detail?.has_more ? <button className="link older-turns" disabled={loadingOlder} onClick={loadOlder}>{loadingOlder ? "加载中…" : "更早的消息"}</button> : null}
                {detail === null && !error ? <TurnsSkeleton /> : null}
                {turns.map((t, i) => (
                  <Turn
                    key={t.id}
                    run={t}
                    items={items[t.id]}
                    live={streaming === t.id}
                    last={i === turns.length - 1}
                    fresh={!opened.current?.has(t.id)}
                    modelLabel={modelName(t.model)}
                    previewKey={turnPreviewKey}
                    onPreview={turnPreview}
                    onPreviewFile={turnPreviewFile}
                    onResume={turnResume}
                    onAnswer={turnAnswer}
                    onRetry={running ? undefined : turnRetry}
                    onExtract={turnExtract}
                    onEdit={turnEdit}
                    onSwitch={turnSwitch}
                    locked={running}
                    forkModes={forkModes[i]}
                    onFork={turnFork}
                    projectId={projectId}
                    sourcesOpen={showPane && sourcesOn && paneSources?.runId === t.id}
                    onSources={turnSources}
                    onOpenLink={turnOpenLink}
                  />
                ))}
                {pending.map((p) => <PendingMessage key={p.id} message={p} />)}
              </div>
              </GenUIHostProvider>
            </div>
            <div className="composer-dock">
              <button type="button" className={`to-bottom ${away ? "shown" : ""}`} aria-label="回到底部" title="回到底部" tabIndex={away ? 0 : -1} aria-hidden={!away}
                onClick={() => {
                  tail.toBottom();
                  setAway(false);
                }}>
                <IconArrowDown size={18} />
              </button>
              {error ? <div className="alert error">{error}</div> : null}
              <QueueStrip
                queue={detail?.queue || []}
                paused={!!detail?.queue_paused}
                running={running && !question}
                onSteer={async (qid) => void (await changeQueued(qid, { steer: true }))}
                onEdit={(qid, prompt) => changeQueued(qid, { prompt })}
                onRemove={async (qid) => void (await changeQueued(qid, null))}
                onResume={resumeQueue}
              />
              {composer}
            </div>
          </>
        )}
      </section>

      {showPane ? (
        <PreviewPane
          previews={openFiles}
          selected={selected}
          onSelect={(f) => { setSelected(f); setSourcesOn(false); setReaderOn(false); }}
          onCloseFile={closeFile}
          sources={paneSources}
          sourcesOn={sourcesOn && !readerOn}
          onSourcesTab={() => { setSourcesOn(true); setReaderOn(false); }}
          onCloseSources={closeSources}
          readers={readers}
          readerUrl={paneReader?.url || null}
          readerOn={readerOn}
          onReaderTab={(url) => { setReaderUrl(url); setReaderOn(true); setSourcesOn(false); }}
          onCloseReader={closeReader}
          onRead={openReader}
          onClose={() => setPaneOpen(false)}
          live={!!streaming && running}
          downloads={downloads}
        />
      ) : null}
      {sheetSources ? <SourcesSheet sources={sheetSources.sources} answer={sheetSources.answer} onClose={() => setSheetSources(null)} /> : null}
    </div>
  );
}

/** Where turns will be, while the conversation loads for the first time: shapes, not a "loading" word. */
function TurnsSkeleton() {
  return (
    <div className="turns-skeleton" aria-label="加载中" role="status">
      <div className="sk sk-user" />
      <div className="sk-row">
        <div className="sk sk-avatar" />
        <div className="sk-lines">
          <div className="sk" />
          <div className="sk" />
          <div className="sk short" />
        </div>
      </div>
    </div>
  );
}

/** Scroll a turn into view and mark it for a moment (where a search result points). */
function reveal(runId: string) {
  const el = document.getElementById(`turn-${runId}`);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  el.classList.add("found");
  setTimeout(() => el.classList.remove("found"), 1800);
}
