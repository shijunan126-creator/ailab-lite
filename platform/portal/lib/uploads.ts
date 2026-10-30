// Attachments being uploaded and checked, by a local key that exists from the moment a file is picked
// (HLD §16.2–16.3). An upload started in the composer is taken over by the message that sends it, never repeated.
import { create } from "zustand";
import { API_BASE, filesByIds, redirectIfUnauthorized, type FileItem } from "./api";

export type UploadStage = "uploading" | "checking" | "ready" | "failed";
export type Upload = {
  key: string;
  filename: string;
  contentType: string;
  size: number;
  stage: UploadStage;
  loaded: number; // bytes sent: real progress while uploading
  fileId?: string;
  error?: string;
  permanent?: boolean; // rejected by the scan: retrying cannot help, the file must be removed or replaced
};

type State = { uploads: Record<string, Upload> };
export const useUploads = create<State>(() => ({ uploads: {} }));

const files = new Map<string, File>(); // the picked file, kept so a failed upload can be retried
const running = new Map<string, Promise<Upload | undefined>>();

function patch(key: string, change: Partial<Upload>) {
  useUploads.setState((s) => (s.uploads[key] ? { uploads: { ...s.uploads, [key]: { ...s.uploads[key], ...change } } } : s));
}

export const getUpload = (key: string): Upload | undefined => useUploads.getState().uploads[key];

/** A file the library already has: nothing to upload, ready at once. */
export function existingUpload(file: Pick<FileItem, "id" | "filename" | "status" | "content_type" | "size">): string {
  const key = `file:${file.id}`;
  useUploads.setState((s) => ({
    uploads: {
      ...s.uploads,
      [key]: {
        key, filename: file.filename, contentType: file.content_type || "", size: file.size || 0, fileId: file.id, loaded: file.size || 0,
        stage: file.status === "ready" ? "ready" : file.status === "rejected" ? "failed" : "checking",
        ...(file.status === "rejected" ? { error: "未通过检查", permanent: true } : {}),
      },
    },
  }));
  if (file.status === "pending") running.set(key, untilChecked(key));
  return key;
}

function send(key: string, file: File, projectId?: string | null): Promise<FileItem> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    xhr.open("POST", `${API_BASE}/v1/files${query}`);
    xhr.timeout = 10 * 60_000;
    xhr.upload.onprogress = (e) => patch(key, { loaded: e.loaded });
    xhr.onload = () => {
      redirectIfUnauthorized({ status: xhr.status } as Response);
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          return resolve(JSON.parse(xhr.responseText) as FileItem);
        } catch {
          return reject(new Error("上传失败"));
        }
      }
      let detail = "";
      try {
        detail = String(JSON.parse(xhr.responseText)?.detail || "");
      } catch {
        // not JSON
      }
      reject(new Error(xhr.status === 413 ? "文件太大" : detail && detail.length < 80 ? detail : `上传失败（${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error("网络中断，上传失败"));
    xhr.ontimeout = () => reject(new Error("上传超时"));
    const form = new FormData();
    form.append("upload", file);
    xhr.send(form);
  });
}

const CHECK_MS = 1000;
const CHECK_LIMIT_MS = 10 * 60_000;

/** Waits for the Worker's scan of an uploaded file. One request per poll covers every file still being checked. */
async function untilChecked(key: string): Promise<Upload | undefined> {
  const started = Date.now();
  for (;;) {
    const upload = getUpload(key);
    if (!upload) return undefined; // removed meanwhile
    if (upload.stage !== "checking") return upload;
    if (Date.now() - started > CHECK_LIMIT_MS) {
      patch(key, { stage: "failed", error: "检查超时" });
      return getUpload(key)!;
    }
    await new Promise((r) => setTimeout(r, CHECK_MS));
    await pollChecks();
  }
}

let polling: Promise<void> | null = null;
function pollChecks(): Promise<void> {
  // every waiting file shares one request per tick
  polling ??= (async () => {
    const waiting = Object.values(useUploads.getState().uploads).filter((u) => u.stage === "checking" && u.fileId);
    if (!waiting.length) return;
    const fresh = await filesByIds(waiting.map((u) => u.fileId!)).catch(() => [] as FileItem[]);
    for (const f of fresh) {
      const u = waiting.find((x) => x.fileId === f.id);
      if (!u) continue;
      if (f.status === "ready") patch(u.key, { stage: "ready" });
      else if (f.status === "rejected") patch(u.key, { stage: "failed", error: "未通过检查", permanent: true });
      else if (f.meta?.processing === "error") patch(u.key, { stage: "failed", error: "检查出错" });
    }
  })().finally(() => {
    polling = null;
  });
  return polling;
}

/** Starts uploading a picked file; the returned key follows it through upload and scan. */
export function startUpload(file: File, projectId?: string | null): string {
  const key = `local:${crypto.randomUUID()}`;
  files.set(key, file);
  useUploads.setState((s) => ({
    uploads: { ...s.uploads, [key]: { key, filename: file.name, contentType: file.type, size: file.size, stage: "uploading", loaded: 0 } },
  }));
  run(key, projectId);
  return key;
}

function run(key: string, projectId?: string | null) {
  const file = files.get(key);
  const task = (async () => {
    if (!file) throw new Error("原文件已不在，请重新选择");
    const item = await send(key, file, projectId);
    patch(key, { fileId: item.id, loaded: file.size, stage: item.status === "ready" ? "ready" : item.status === "rejected" ? "failed" : "checking",
      ...(item.status === "rejected" ? { error: "未通过检查", permanent: true } : {}) });
    return untilChecked(key);
  })().catch((err: Error) => {
    patch(key, { stage: "failed", error: err.message });
    return getUpload(key);
  });
  running.set(key, task);
}

/** The upload's outcome: resolves once it is ready or has failed (never rejects). */
export function settle(key: string): Promise<Upload | undefined> {
  const upload = getUpload(key);
  if (!upload) return Promise.resolve(undefined);
  if (upload.stage === "ready" || upload.stage === "failed") return Promise.resolve(upload);
  return running.get(key) ?? (upload.stage === "checking" ? untilChecked(key) : Promise.resolve(upload));
}

/** Redoes only what failed: a failed upload uploads again, a failed check checks again; a ready file stays as it is. */
export function retryUpload(key: string, projectId?: string | null): boolean {
  const upload = getUpload(key);
  if (!upload || upload.stage !== "failed" || upload.permanent) return false;
  if (upload.fileId) {
    patch(key, { stage: "checking", error: undefined });
    running.set(key, untilChecked(key));
    return true;
  }
  if (!files.has(key)) return false; // the browser no longer has the file: it must be picked again
  patch(key, { stage: "uploading", loaded: 0, error: undefined });
  run(key, projectId);
  return true;
}

export function canRetry(key: string): boolean {
  const upload = getUpload(key);
  return !!upload && upload.stage === "failed" && !upload.permanent && (!!upload.fileId || files.has(key));
}

export function forgetUpload(key: string) {
  files.delete(key);
  running.delete(key);
  useUploads.setState((s) => {
    const { [key]: _, ...rest } = s.uploads;
    return { uploads: rest };
  });
}
