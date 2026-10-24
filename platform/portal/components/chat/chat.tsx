"use client";

import { useCallback, useEffect, useMemo, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Conversation, ConversationDetail, downloadUrl, errorMessage, filesByIds, MAX_ATTACHMENTS, ModelInfo, Preview, Project, Run, streamRun } from "../../lib/api";
import { cacheConversation, cachedConversation } from "../../lib/conversation-cache";
import { mergeTurns, mergePreviews, refreshConversation } from "../../lib/conversation-pages";
import { isTerminal } from "../../lib/format";
import { projectsChanged, useProjects } from "../../lib/projects";
import { conversationsChanged, conversationTitled } from "../app-shell";
import { CONVERSATION_MOVED } from "../conversation-menu";
import { IconBranch, IconChevronRight, IconFolder, IconSlides } from "../icons";
import ProjectHome from "../project-home";
import Composer, { Attachment, SendOptions } from "./composer";
import PreviewPane from "./preview-pane";
import QuotaBanner from "./quota-banner";
import Turn, { ForkMode, Item, reduceEvent } from "./turn";

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
      .then((list) => setFiles(list.filter((f) => f.status === "ready").map((f) => ({ id: f.id, filename: f.filename, status: f.status }))))
      .catch(() => undefined);
  }, [active]);
  return files;
}

export default function Chat({ id, project }: { id: string | null; project?: Project | null }) {
  const router = useRouter();
  const initialFiles = useInitialFiles(!id);
  const projects = useProjects();
  const { models, model, setModel } = useModels();
  // a conversation opened before in this tab shows at once; the fresh copy replaces it when it arrives
  const [detail, setDetail] = useState<ConversationDetail | null>(() => (id ? cachedConversation(id) : null));
  const [items, setItems] = useState<Record<string, Item[]>>({}); // run id -> live items (runs streamed in this page)
  const [livePreviews, setLivePreviews] = useState<Preview[]>([]);
  const [paneOpen, setPaneOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
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

  const loadDetail = useCallback(async () => {
    if (!id) return null;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const seq = ++loadSeq.current;
    try {
      const d = await api<ConversationDetail>(`/v1/conversations/${id}?limit=20`, { signal: controller.signal });
      if (seq !== loadSeq.current) return null;
      setDetail((previous) => refreshConversation(previous, d));
      return d;
    } catch (err: any) {
      if (!controller.signal.aborted && seq === loadSeq.current) setError(errorMessage(err));
      return null;
    }
  }, [id]);

  const follow = useCallback(
    (run: Run) => {
      if (actionController.current.signal.aborted) return new AbortController();
      streamController.current?.abort();
      const controller = new AbortController();
      streamController.current = controller;
      setStreaming(run.id);
      setItems((prev) => ({ ...prev, [run.id]: prev[run.id] || [] }));
      streamRun(
        run.id,
        (event) => {
          if (controller.signal.aborted) return;
          if (event.seq) eventCursors.current[run.id] = event.seq;
          setItems((prev) => ({ ...prev, [run.id]: reduceEvent(prev[run.id] || [], event) }));
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
          if (["run.status", "artifact.created", "run.completed", "run.failed", "run.canceled"].includes(event.type)) loadDetail();
        },
        controller.signal,
        () => {
          if (controller.signal.aborted) return;
          setStreaming(null);
          loadDetail().then((d) => {
            // a file made or changed by this turn opens once the turn is done, not on every intermediate render
            const file = updatedPreview.current[run.id];
            delete updatedPreview.current[run.id];
            const finished = d?.turns.find((t) => t.id === run.id);
            if (!file || finished?.status !== "succeeded" || controller.signal.aborted) return;
            if (!window.matchMedia("(min-width: 761px)").matches) return; // on a phone the pane covers the chat: the file card opens it
            setSelected(file);
            setPaneOpen(true);
          });
          conversationsChanged();
        },
        eventCursors.current[run.id] || 0
      ).catch((err) => {
        if (!controller.signal.aborted) {
          setStreaming(null);
          setError(errorMessage(err));
          loadDetail();
        }
      });
      return controller;
    },
    [loadDetail]
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
      if (last && (!isTerminal(last.status) || last.status === "interrupted")) controller = follow(last);
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
    } else if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items, detail]);

  const previews = useMemo(() => {
    const byFile = new Map<string, Preview>();
    for (const p of [...(detail?.previews || []), ...livePreviews]) {
      byFile.delete(p.filename);
      byFile.set(p.filename, p);
    }
    return [...byFile.values()].filter((p) => p.kind);
  }, [detail, livePreviews]);

  const downloads = useMemo(() => {
    const out: Record<string, string> = {};
    for (const t of detail?.turns || []) for (const a of t.artifacts || []) out[a.filename] = downloadUrl(a.download_url);
    return out;
  }, [detail]);

  const modelName = (m: string) => models.find((x) => x.id === m)?.name || m;
  const ext = (name: string) => name.split(".").pop()?.toLowerCase() || "";
  // Previews are keyed by the file's name in the sandbox; deliver_file may have given it another name.
  // Then the one preview of the same kind that this turn rendered is the delivered file's.
  const previewKey = (run: Run, filename: string): string | null => {
    if (previews.some((p) => p.filename === filename)) return filename;
    const own = (run.result?.previews || []).filter((p) => ext(p.filename) === ext(filename));
    return own.length === 1 && previews.some((p) => p.filename === own[0].filename) ? own[0].filename : null;
  };
  const turns = detail?.turns || [];
  // turns already there when the conversation opened appear with it; only turns that arrive afterwards animate in
  const opened = useRef<Set<string> | null>(null);
  if (detail && opened.current === null) opened.current = new Set(detail.turns.map((t) => t.id));
  // a conversation that has worked in files can only branch into work: chat has no workspace to carry on in
  const forkModes = useMemo(() => {
    let worked = false;
    return turns.map((t) => {
      worked ||= t.options?.mode !== "chat";
      return (worked ? ["work"] : ["chat", "work"]) as ForkMode[];
    });
  }, [turns]);
  const projectId = id ? detail?.project_id || null : project?.id || null;
  const crumb = id && projectId ? projects?.find((p) => p.id === projectId) : null;
  const running = turns.some((t) => !isTerminal(t.status));
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
        router.push(`/c/${convId}`);
        return true;
      }
      setDetail((d) => (d ? { ...d, turns: [...d.turns, run] } : d));
      stick.current = true;
      follow(run);
      conversationsChanged();
      return true;
    } catch (err: any) {
      const message = errorMessage(err);
      setError(/^409 /.test(String(err?.message)) && !/[\u4e00-\u9fff]/.test(message) ? "上一条消息还在回答中，请稍候或先停止它" : message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function answer(run: Run, questionId: string, value: string) {
    const resumed = await api<Run>(`/v1/runs/${run.id}/answer`, {
      method: "POST", signal: actionController.current.signal,
      body: JSON.stringify({ question_id: questionId, answer: value }),
    });
    setDetail((d) => (d ? { ...d, turns: d.turns.map((t) => (t.id === run.id ? resumed : t)) } : d));
    follow(resumed);
  }

  async function resume(run: Run) {
    setError("");
    try {
      const resumed = await api<Run>(`/v1/runs/${run.id}/resume`, { method: "POST", signal: actionController.current.signal });
      setDetail((d) => (d ? { ...d, turns: d.turns.map((t) => (t.id === run.id ? resumed : t)) } : d));
      stick.current = true;
      follow(resumed);
    } catch (err: any) {
      setError(errorMessage(err));
    }
  }

  /** A new version of a turn (regenerated, or its message edited): it and everything after it give way to the new one. */
  function branchTo(old: Run, fresh: Run) {
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
  async function edit(run: Run, prompt: string): Promise<boolean> {
    setError("");
    try {
      const fresh = await api<Run>(`/v1/runs/${run.id}/edit`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ prompt, file_ids: null, model: model || null, mode: null }),
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
    try {
      const page = await api<ConversationDetail>(`/v1/conversations/${id}/active`, {
        method: "POST", signal: actionController.current.signal,
        body: JSON.stringify({ run_id: runId }),
      });
      for (const t of page.turns) opened.current?.add(t.id); // a version switch is not new content arriving
      ++loadSeq.current; // a detail load already in flight would bring the old path back
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
    const active = turns.find((t) => !isTerminal(t.status));
    if (active) await api(`/v1/runs/${active.id}/cancel`, { method: "POST" }).catch(() => undefined);
    loadDetail();
  }

  const showPane = paneOpen && previews.length > 0;
  const composer = (
    <>
    <QuotaBanner refresh={streaming} />
    <Composer
      models={models}
      model={model}
      onModel={setModel}
      busy={busy}
      running={running && !question}
      onSend={send}
      onStop={stop}
      autoFocus
      placeholder={question ? "回答上面的问题" : id ? "继续对话，比如「第三页换成图表」「再加一列占比」" : project ? `在「${project.name}」里开始新对话` : undefined}
      projectId={projectId}
      initialFiles={initialFiles}
      context={detail?.context ? { used: detail.context.used_tokens, compactAt: detail.context.compact_at_tokens } : undefined}
    />
    </>
  );

  return (
    <div className={`chat-layout ${showPane ? "with-preview" : ""}`}>
      <section className="chat-main">
        {!id && project ? (
          <ProjectHome project={project} composer={composer} error={error} />
        ) : !id ? (
          <>
            <div className="chat-home">
              <h1>有什么我能帮你的吗？</h1>
            </div>
            <div className="composer-dock">
              {error ? <div className="alert error">{error}</div> : null}
              {composer}
            </div>
          </>
        ) : (
          <>
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
            <div
              className="messages"
              ref={scroller}
              onScroll={(e) => {
                const el = e.currentTarget;
                stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                if (el.scrollTop < 80) void loadOlder();
              }}
            >
              <div className="messages-inner">
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
                    modelName={modelName}
                    previewKey={previewKey}
                    onPreview={(f) => {
                      setSelected(f);
                      setPaneOpen(true);
                    }}
                    onResume={resume}
                    onAnswer={answer}
                    onRetry={running ? undefined : retry}
                    onEdit={edit}
                    onSwitch={switchTo}
                    locked={running}
                    forkModes={forkModes[i]}
                    onFork={fork}
                  />
                ))}
              </div>
            </div>
            <div className="composer-dock">
              {error ? <div className="alert error">{error}</div> : null}
              {!paneOpen && previews.length ? (
                <button className="preview-reopen" aria-label={`打开预览（${previews.length}）`} onClick={() => { setSelected((value) => value || previews[previews.length - 1]?.filename || null); setPaneOpen(true); }}>
                  <IconSlides size={14} /> <span>预览</span> <span className="count">{previews.length}</span>
                </button>
              ) : null}
              {composer}
            </div>
          </>
        )}
      </section>

      {showPane ? (
        <PreviewPane
          previews={previews}
          selected={selected}
          onSelect={setSelected}
          onClose={() => setPaneOpen(false)}
          live={!!streaming && running}
          downloads={downloads}
        />
      ) : null}
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
