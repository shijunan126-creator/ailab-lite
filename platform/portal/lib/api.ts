// The browser only ever talks to this Portal's own /api proxy (see app/api/v1). The access token
// is an httpOnly cookie that the proxy turns into an Authorization header on the server, so no
// token exists in this bundle, in URLs, or in anything JavaScript can read.
import type { UIState } from "./ui-state";

export const API_BASE = "/api";

/** A 401 means the session is gone or the token was rotated: back to the login page. */
export function redirectIfUnauthorized(resp: Response): void {
  if (resp.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
}

export type Role = "user" | "dept_admin" | "platform_admin" | "auditor";
export type Ref = { id: string; name: string };
export type Me = {
  id: string;
  username: string | null;
  display_name: string;
  email: string;
  roles?: Role[];
  dept?: Ref | null;
  can_admin?: boolean;
};

export type Run = {
  id: string;
  conversation_id?: string | null;
  title: string;
  prompt: string;
  status: string;
  model: string;
  summary: string;
  error: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  work_ms?: number | null;
  artifacts?: Artifact[];
  input_files?: { file_id: string; filename: string }[];
  options?: { mode?: "chat" | "work"; skill?: string; skill_title?: string };
  template?: { name: string; source: "template" | "file" } | null;
  result?: {
    file_wait?: { action: "extract-text" | "image-reference" };
    previews?: Preview[];
    sources?: { title: string; url: string; site: string }[];
    pause?: { kind: "quota"; window: "daily_tokens" | "monthly_tokens"; resets_at: string }; // why an interrupted run rests
    [key: string]: any;
  };
  usage?: { prompt_tokens?: number; completion_tokens?: number; calls?: number };
  resumable?: boolean; // a failure the run can continue from (its checkpoint survived)
  retry_of?: string | null; // the turn this one regenerated
  edited_from?: string | null; // the turn whose message was edited into this one
  versions?: { index: number; count: number; ids: string[] }; // the turns answering the same point of the conversation
  steered?: { id: string; prompt: string }[]; // messages the user added while it was running, in the order it took them
  ui_state?: UIState | null; // what the reader set on the answer's generated UI (lib/ui-state)
};

/** A rendered preview of an office file a run produced (see the Broker's refresh_previews). */
export type Preview = {
  filename: string;
  version: number;
  run_id?: string;
  kind?: "pages" | "sheets" | "html" | "pdf";
  pages?: string[]; // "<n>/p-01.jpg", relative to the run's previews
  pdf?: string; // "<n>/document.pdf"
  page_count?: number | null;
  data?: string; // "<n>/sheets.json"
  file_id?: string; // kind "html": the delivered file itself, rendered in the pane
  errors?: string[];
  error?: string;
};

export type Skill = {
  name: string;
  title: string;
  description: string;
  builtin: boolean;
  implicit?: boolean; // driven by another entry (e.g. the conversation menu): not offered as a skill of its own
  enabled: boolean;
  scope: "builtin" | "personal";
  file_id?: string; // personal skills: the file in the library that defines it
  filename?: string;
  kind?: "md" | "zip";
  files?: number;
  error?: string | null; // why a personal skill file cannot be used
};

export type Conversation = {
  memory_policy?: { read?: boolean; write?: boolean };
  id: string;
  title: string;
  model: string;
  project_id?: string | null;
  created_at: string;
  updated_at: string;
  last_status?: string | null;
  forked_from?: { conversation_id: string; run_id: string; mode?: "chat" | "work" } | null;
  pinned_at?: string | null;
  active_run_id?: string | null; // the last turn of the version on show
  moved_files?: number | null; // only in the reply to a move that carried the files along
};

export type ConversationDetail = Conversation & {
  turns: Run[];
  previews: Preview[];
  has_more: boolean;
  context?: { used_tokens: number | null; model: string | null; compact_at_tokens: number | null };
  queue?: QueuedMessage[];
  queue_paused?: boolean; // the turn before it did not finish well, or the path changed: the user decides
};

/** A message written while an answer is still running. steer: delivered into that answer at its next step. */
export type QueuedMessage = {
  id: string;
  prompt: string;
  file_ids: string[];
  model: string;
  mode: "chat" | "work";
  skill: string | null;
  steer: boolean;
  state: "queued" | "steered" | "started" | "canceled";
  created_at: string;
};

export type NotificationKind = "run.succeeded" | "run.failed" | "run.waiting" | "schedule.succeeded" | "schedule.failed" | "share.received";
export type Notice = {
  id: string;
  kind: NotificationKind;
  title: string;
  conversation_id: string | null;
  run_id: string | null;
  share_id: string | null;
  created_at: string;
  read_at: string | null;
};

export type Person = { id: string; username: string; display_name: string };

export type Share = {
  id: string;
  kind: "conversation" | "file";
  conversation_id: string | null;
  file_id: string | null;
  audience: "people" | "everyone";
  recipients: Person[];
  title: string;
  url: string; // the Portal page, /s/<id>
  through_run_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ShareFile = Pick<FileItem, "id" | "filename" | "content_type" | "size" | "thumbnail_url"> & {
  download_url: string; // /api/v1/shares/<id>/files/<file>/content: reachable only through the share
};

export type ShareView = {
  share: Share;
  owner: { display_name: string };
  title: string;
  turns: {
    id: string;
    prompt: string;
    answer: string;
    ui_state?: UIState | null;
    created_at: string;
    mode: "chat" | "work";
    artifacts: ShareFile[];
    attachments: { filename: string }[];
  }[];
  file?: ShareFile | null; // a file share: the file itself
  next_cursor: string | null;
};

export type BrandKind = "logo" | "docx";
export type BrandAsset = {
  file_id: string;
  filename: string;
  status: string;
  size: number;
  analysis: Record<string, any> | null;
  thumbnail_url: string | null;
};
export type Brand = {
  enabled: boolean;
  name: string;
  colors: { primary: string; accent: string };
  font: string | null;
  version: number;
  updated_at: string | null;
  assets: Partial<Record<BrandKind, BrandAsset | null>>;
};

/** A PPT template: the company's (everyone sees them) or one of the user's own. Using one means referencing its look and layouts. */
export type Template = {
  id: string;
  kind: "pptx";
  scope: "company" | "personal";
  name: string;
  status: "pending" | "ready" | "rejected" | "failed";
  reason: string | null;
  preview_status: "pending" | "ready" | "failed" | "unsupported";
  thumbnail_url: string | null; // without the /api prefix
  layouts: number | null;
  slides: number | null;
  size: number;
  filename: string;
  created_at: string;
  can_edit: boolean;
};
export type TemplateList = { company: Template[]; personal: Template[] };
/** What a message asks to follow: a saved template, or a pptx from the library just for this message. */
export type TemplateRef = { id: string } | { file_id: string };

export type SearchHit = {
  conversation_id: string;
  title: string;
  project_id: string | null;
  run_id: string | null;
  match: "title" | "prompt" | "answer";
  snippet: string;
  highlight: [number, number][];
  updated_at: string;
};

export type Schedule = {
  id: string;
  title: string;
  prompt: string;
  mode: "chat" | "work";
  skill: string | null;
  model: string | null;
  project_id: string | null;
  schedule: { kind: "daily" | "weekdays" | "weekly"; time: string; days?: number[] };
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_conversation_id: string | null;
  last_status: string | null;
  last_error: string | null;
};

export function previewUrl(runId: string, name: string): string {
  return `${API_BASE}/v1/runs/${runId}/previews/${name}`;
}

export type Artifact = {
  id: string;
  file_id: string;
  filename: string;
  content_type: string;
  size: number;
  download_url: string;
};

export type ModelInfo = {
  id: string;
  name: string;
  description: string;
  weight: number;
  tools: boolean;
  capabilities?: string[];
  default: boolean;
  context_tokens?: number;
  input_budget_tokens?: number;
};

export type Limit = { used: number; limit: number };
export type TokenWindow = Limit & { reserved: number; percent: number; resets_at: string }; // weighted tokens; percent counts reservations
export type UsageAlert = { code: "near_limit" | "exceeded"; window: "daily_tokens" | "monthly_tokens"; resets_at: string };
export type Usage = {
  daily_tokens: TokenWindow;
  monthly_tokens: TokenWindow;
  library_bytes: Limit;
  resets_at_utc: string;
  alerts: UsageAlert[];
};

export type Info = {
  env: string;
  default_model: string | null;
  sandbox_provider: string;
  storage_backend: string;
  max_upload_bytes: number;
};

export type FileItem = {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  status: string;
  version: number;
  meta: Record<string, any>;
  created_at: string;
  download_url: string;
  project_id?: string | null;
  starred?: boolean;
  thumbnail_url?: string | null; // a small WebP the Worker made; absent for non-images and before processing
  preview?: FilePreview | null; // null until someone first opens it
  lineage_id?: string; // every version of a file shares it
  versions?: number;
  is_head?: boolean;
  deleted_at?: string | null; // in the trash since
  purge_at?: string | null; // removed for good at
  purged?: boolean; // an old version whose content is gone
};

/** How a library file can be shown in place (GET /v1/files/{id}/preview). Rendered once, on first open, then cached. */
export type FilePreview = {
  status: "ready" | "pending" | "failed" | "unsupported";
  kind?: "pages" | "sheets" | "image" | "text" | null;
  pages?: string[];
  page_count?: number | null; // the whole document; pages may stop earlier
  data?: string; // sheets.json, same format as a run's sheet previews
  image?: string;
  text?: string;
  format?: "markdown" | "code" | "plain";
  language?: string | null;
  truncated?: boolean;
  reason?: string;
};

export function filePreviewUrl(fileId: string, name: string): string {
  return `${API_BASE}/v1/files/${fileId}/preview/${encodeURIComponent(name)}`;
}

/** A project groups conversations and files; deleting it deletes both. */
export type Project = {
  id: string;
  name: string;
  instructions?: string; // the standing brief every conversation in the project starts with
  created_at: string;
  updated_at: string;
  conversations: number;
  files: number;
};

export type EventItem = {
  seq: number;
  type: string;
  payload: any;
  created_at?: string;
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  if (init.body && typeof init.body === "string") headers["Content-Type"] = "application/json";
  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: "no-store" });
  redirectIfUnauthorized(resp);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

const QUOTA_WINDOW: Record<string, string> = { daily_tokens: "今日", monthly_tokens: "本月" };

/** "8 分钟后", "3 小时 20 分钟后", "2 天后": how long until a quota window starts over; "" once it has. */
export function untilReset(resetsAt: string, now = Date.now()): string {
  const ms = new Date(resetsAt).getTime() - now;
  if (Number.isNaN(ms) || ms <= 0) return "";
  const minutes = Math.ceil(ms / 60000);
  if (minutes < 60) return `${minutes} 分钟后`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} 小时 ${minutes % 60} 分钟后` : `${hours} 小时后`;
  return `${Math.round(hours / 24)} 天后`;
}

export function quotaMessage(window: string, resetsAt: string, now = Date.now()): string {
  const left = untilReset(resetsAt, now);
  return left ? `${QUOTA_WINDOW[window] || ""}额度已用完，${left}恢复` : `${QUOTA_WINDOW[window] || ""}额度已恢复`;
}

/** The parsed `detail` of a failed API call, or null when the error is not an API response. */
function errorDetail(err: unknown): any {
  const text = String((err as any)?.message || err || "");
  const m = /^(\d{3}) ([\s\S]*)$/.exec(text);
  if (!m) return null;
  try {
    return JSON.parse(m[2])?.detail ?? null;
  } catch {
    return null;
  }
}

/** What to tell the user about a failed request: the API's own detail ("409 {"detail":"…"}" → "…"), else the text. */
export function errorMessage(err: unknown): string {
  const text = String((err as any)?.message || err || "");
  const detail = errorDetail(err);
  if (detail === null) return text;
  if (typeof detail === "string" && detail) return detail;
  if (detail?.code === "quota_exceeded" && detail.resets_at) return quotaMessage(detail.window, detail.resets_at);
  if (typeof detail?.message === "string" && detail.message) return detail.message; // {"code", "message"} errors
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  const m = /^(\d{3}) ([\s\S]*)$/.exec(text);
  return m ? m[2] : text;
}

/** The machine-readable code of a failed API call ({"detail": {"code": "…"}}), for branching that must not match on wording. */
export function errorCode(err: unknown): string | null {
  const detail = errorDetail(err);
  return typeof detail?.code === "string" ? detail.code : null;
}

/** The same-origin render proxy: the original page, re-served so the sandboxed iframe can show it. */
export function readerRenderUrl(url: string): string {
  return `${API_BASE}/v1/reader/render?url=${encodeURIComponent(url)}`;
}

export function downloadUrl(path: string): string {
  return `${API_BASE}${path}`; // same-origin: the session cookie authorizes the download
}

/** Several files by id in one request (unknown or foreign ids are left out). */
export async function filesByIds(ids: string[], signal?: AbortSignal): Promise<FileItem[]> {
  const out: FileItem[] = [];
  for (let i = 0; i < ids.length; i += 200) {
    // the API takes at most 200 ids per request
    const query = ids.slice(i, i + 200).map((id) => `ids=${encodeURIComponent(id)}`).join("&");
    out.push(...(await api<FileItem[]>(`/v1/files?limit=200&${query}`, { signal })));
  }
  return out;
}

/** How many files one message may carry (the API enforces the same number). */
export const MAX_ATTACHMENTS = 50;

/** One zip of several library files, streamed by the platform. */
export function archiveUrl(ids: string[]): string {
  return `${API_BASE}/v1/files/archive?${ids.map((id) => `ids=${encodeURIComponent(id)}`).join("&")}`;
}

/** Upload one file to the library (into a project when given). Throws with the server's reason. */
export async function uploadFile(file: File, projectId?: string | null): Promise<FileItem> {
  const form = new FormData();
  form.append("upload", file);
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const resp = await fetch(`${API_BASE}/v1/files${query}`, { method: "POST", body: form });
  redirectIfUnauthorized(resp);
  if (!resp.ok) throw new Error(`${file.name}：上传失败（${resp.status}）`);
  return (await resp.json()) as FileItem;
}

const RETRY_MS = [500, 1000, 2000, 4000, 8000];
const MAX_FAILURES = 8; // consecutive attempts that delivered nothing

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

/** Follow a run's events until it ends. A dropped connection (network, proxy, server restart) resumes after the
 *  last event received, so nothing is missed or shown twice; only repeated failures give up. */
export async function streamRun(
  runId: string,
  onEvent: (event: EventItem) => void,
  signal: AbortSignal,
  onEnd?: () => void,
  after = 0
): Promise<void> {
  let failures = 0;
  while (!signal.aborted) {
    let ended = false;
    let delivered = false;
    try {
      const resp = await fetch(`${API_BASE}/v1/runs/${runId}/events?after=${after}`, { signal });
      redirectIfUnauthorized(resp);
      if (resp.status === 401 || resp.status === 404) throw Object.assign(new Error(`event stream ${resp.status}`), { fatal: true });
      if (!resp.ok || !resp.body) throw new Error(`event stream ${resp.status}`);
      ended = await readEvents(resp.body, (event) => {
        if (event.seq && event.seq <= after) return; // already shown before the reconnect
        if (event.seq) after = event.seq;
        delivered = true;
        onEvent(event);
      });
    } catch (err: any) {
      if (signal.aborted) return;
      if (err?.fatal || failures + 1 >= MAX_FAILURES) throw err;
    }
    if (ended) {
      onEnd?.();
      return;
    }
    if (signal.aborted) return;
    failures = delivered ? 0 : failures + 1;
    if (failures >= MAX_FAILURES) throw new Error("连接多次中断，请刷新后重试");
    const base = RETRY_MS[Math.min(failures, RETRY_MS.length - 1)];
    await sleep(base + Math.random() * base * 0.3, signal);
  }
}

/** Reads one SSE response. True when the server said the run is over (stream.end). */
async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (event: EventItem) => void): Promise<boolean> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return false;
      buffer += decoder.decode(value, { stream: true });
      let idx = buffer.indexOf("\n\n");
      while (idx >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const event = parseChunk(chunk);
        if (event) {
          if (event.type === "stream.end") return true;
          onEvent(event);
        }
        idx = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function parseChunk(chunk: string): EventItem | null {
  let type = "message";
  let id = 0;
  const data: string[] = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("id:")) id = Number(line.slice(3).trim());
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    return { seq: id, type, payload: JSON.parse(data.join("\n")) };
  } catch {
    return { seq: id, type, payload: { raw: data.join("\n") } };
  }
}

export type SkillPolicy = { scope: string; name: string; state: "allowed" | "review" | "disabled" };
export type SkillReview = { file_id: string; name: string; owner_id: string };
