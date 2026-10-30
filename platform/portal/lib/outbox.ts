// Messages the user has sent that are not yet a run (HLD §16.2–16.4). A message shows at once; its attachments
// finish uploading and checking in the background, and only when all of them are ready (and Chat's preparation has
// passed) is the model asked to answer. Kept outside React so it survives navigation from the new-chat page.
import { create } from "zustand";
import { api, errorCode, errorMessage, type Conversation, type QueuedMessage, type Run, type TemplateRef } from "./api";
import { canRetry, forgetUpload, getUpload, retryUpload, settle } from "./uploads";

export type SendOptions = { mode: "chat" | "work"; skill?: string; template?: TemplateRef };

export type Pending = {
  id: string; // client_send_id: a retry after a lost response finds the same run instead of starting another
  conversationId: string | null; // null: a new conversation, created when the message goes out
  projectId: string | null;
  prompt: string;
  attachments: string[]; // upload keys, in the order the user attached them
  options: SendOptions;
  model: string;
  vision: boolean; // the chosen model reads images itself; otherwise their text is prepared first
  steer: boolean;
  queued: boolean; // sent while an answer was running: it waits behind it (or goes into it, steer)
  stage: "preparing" | "sending" | "failed" | "sent";
  error?: string;
  attempt: number;
  created: number;
  sent?: { run?: Run; queued?: QueuedMessage; conversation?: Conversation };
};

type State = { pending: Record<string, Pending> };
export const useOutbox = create<State>(() => ({ pending: {} }));

function patch(id: string, change: Partial<Pending>) {
  useOutbox.setState((s) => (s.pending[id] ? { pending: { ...s.pending, [id]: { ...s.pending[id], ...change } } } : s));
}
const get = (id: string) => useOutbox.getState().pending[id];

let lastCreated = 0;

export function enqueue(message: Omit<Pending, "id" | "stage" | "attempt" | "created">): string {
  const id = crypto.randomUUID();
  lastCreated = Math.max(Date.now(), lastCreated + 1);
  useOutbox.setState((s) => ({ pending: { ...s.pending, [id]: { ...message, id, stage: "preparing", attempt: 1, created: lastCreated } } }));
  void process(id, 1);
  return id;
}

/** Sends the message again: only failed uploads or checks are redone; files that are ready stay ready. */
export function retry(id: string): boolean {
  const p = get(id);
  if (!p || p.stage !== "failed") return false;
  for (const key of p.attachments) if (getUpload(key)?.stage === "failed" && canRetry(key)) retryUpload(key, p.projectId);
  const attempt = p.attempt + 1;
  patch(id, { stage: "preparing", error: undefined, attempt });
  void process(id, attempt);
  return true;
}

/** Drops a message that has not gone out (and the uploads only it used). */
export function discard(id: string) {
  const p = get(id);
  if (!p || p.stage === "sending") return;
  remove(id);
}

export function remove(id: string) {
  const attachments = get(id)?.attachments || [];
  useOutbox.setState((s) => {
    const { [id]: _, ...rest } = s.pending;
    return { pending: rest };
  });
  for (const key of attachments) {
    if (!Object.values(useOutbox.getState().pending).some((p) => p.attachments.includes(key))) forgetUpload(key);
  }
}

const PREPARE_MS = 120_000;

async function prepare(p: Pending, fileIds: string[], images: boolean, stale: () => boolean) {
  if (!fileIds.length || !(p.options.mode === "chat" || (images && !p.vision))) return;
  const deadline = Date.now() + PREPARE_MS;
  for (;;) {
    try {
      await api("/v1/chat/prepare", { method: "POST", body: JSON.stringify({ file_ids: fileIds, model: p.model, mode: p.options.mode, prompt: p.prompt }) });
      return;
    } catch (err) {
      if (stale() || errorCode(err) !== "chat_attachment_processing" || Date.now() >= deadline) throw err;
    }
    await new Promise((r) => setTimeout(r, 1500));
    if (stale()) return;
  }
}

/** Messages to the same conversation go out in the order they were sent. */
async function turnOf(p: Pending) {
  for (;;) {
    const earlier = Object.values(useOutbox.getState().pending).filter(
      (o) => o.created < p.created && o.conversationId === p.conversationId && o.projectId === p.projectId && (o.stage === "preparing" || o.stage === "sending")
    );
    if (!earlier.length) return;
    await new Promise<void>((resolve) => {
      const stop = useOutbox.subscribe(() => {
        stop();
        resolve();
      });
    });
  }
}

async function dispatch(p: Pending, fileIds: string[]): Promise<Pending["sent"]> {
  const body = { prompt: p.prompt, model: p.model || undefined, file_ids: fileIds, ...p.options };
  let conversation: Conversation | undefined = p.sent?.conversation;
  if (!p.conversationId && !conversation) {
    conversation = await api<Conversation>("/v1/conversations", { method: "POST", body: JSON.stringify({ model: p.model, project_id: p.projectId || undefined }) });
    patch(p.id, { sent: { conversation } }); // a retry reuses it rather than creating another
  }
  const id = p.conversationId || conversation!.id;
  const asMessage = () => api<Run>(`/v1/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ ...body, client_send_id: p.id }) });
  const asQueued = () => api<QueuedMessage>(`/v1/conversations/${id}/queue`, { method: "POST", body: JSON.stringify({ ...body, steer: p.steer, client_send_id: p.id }) });
  if (p.queued && p.conversationId) {
    try {
      return { queued: await asQueued() };
    } catch (err) {
      // the answer finished while this was prepared: it goes out as a normal message
      if (errorCode(err) !== "conversation_idle") throw err;
    }
  }
  try {
    return { run: await asMessage(), conversation };
  } catch (err) {
    // another answer started meanwhile (an earlier message of this conversation): wait behind it
    if (errorCode(err) === "conversation_busy" && p.conversationId) return { queued: await asQueued() };
    throw err;
  }
}

async function process(id: string, attempt: number) {
  const stale = () => get(id)?.attempt !== attempt;
  const p = get(id);
  if (!p) return;
  const settling = p.attachments.map((key) => settle(key));
  // the first failure fails the message at once; the other files go on and stay ready for the retry
  const firstFailure = new Promise<"failed">((resolve) => settling.forEach((s) => s.then((u) => (!u || u.stage !== "ready") && resolve("failed"))));
  if ((await Promise.race([firstFailure, Promise.all(settling)])) === "failed") {
    if (stale()) return;
    // the whole message waits: never send it with some of its files missing
    const gone = p.attachments.some((key) => !getUpload(key));
    return patch(id, { stage: "failed", error: gone ? "附件已不在，请重新选择" : undefined });
  }
  if (stale()) return;
  const uploads = await Promise.all(settling);
  const fileIds = uploads.map((u) => u!.fileId!);
  const images = uploads.some((u) => u!.contentType.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|tiff?|avif)$/i.test(u!.filename));
  try {
    await prepare(get(id)!, fileIds, images, stale);
    if (stale()) return;
    await turnOf(get(id)!);
    if (stale()) return;
    patch(id, { stage: "sending" });
    const sent = await dispatch(get(id)!, fileIds);
    if (stale()) return;
    // Messages typed on the blank page while its first send was preparing belong to that new conversation.
    useOutbox.setState((state) => ({ pending: Object.fromEntries(Object.entries(state.pending).map(([key, item]) => {
      if (key === id) return [key, { ...item, stage: "sent", sent: { ...item.sent, ...sent } }];
      if (!p.conversationId && sent?.conversation && !item.conversationId && item.projectId === p.projectId && item.created > p.created) {
        return [key, { ...item, conversationId: sent.conversation.id }];
      }
      return [key, item];
    })) }));
  } catch (err) {
    if (!stale()) patch(id, { stage: "failed", error: errorMessage(err) });
  }
}
