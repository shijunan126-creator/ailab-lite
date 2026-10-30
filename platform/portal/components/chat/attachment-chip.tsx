"use client";

import { useUploads, type Upload } from "../../lib/uploads";
import { ExtTile } from "../library/file-tile";
import { IconX } from "../icons";

const STAGE: Record<Upload["stage"], string> = { uploading: "上传中", checking: "检查中", ready: "", failed: "失败" };

/** The state of one attachment, always in the tile's top-right corner: real upload progress, then an indeterminate
 * mark while it is checked, an error mark if it failed, nothing once ready (HLD §16.3). */
function Corner({ upload }: { upload: Upload }) {
  if (upload.stage === "ready") return null;
  if (upload.stage === "failed") return <span className="att-corner failed" aria-hidden="true">!</span>;
  if (upload.stage === "uploading" && upload.size > 0) {
    const pct = Math.min(100, Math.round((upload.loaded / upload.size) * 100));
    return (
      <span className="att-corner" aria-hidden="true">
        <svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" className="track" /><circle cx="10" cy="10" r="7.5" className="bar" pathLength={100} strokeDasharray={`${pct} 100`} /></svg>
      </span>
    );
  }
  return <span className="att-corner spin" aria-hidden="true"><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" className="track" /><circle cx="10" cy="10" r="7.5" className="bar" pathLength={100} strokeDasharray="28 100" /></svg></span>;
}

export default function AttachmentChip({ uploadKey, onRemove, onRetry, onOpen }: { uploadKey: string; onRemove?: () => void; onRetry?: () => void; onOpen?: () => void }) {
  const upload = useUploads((s) => s.uploads[uploadKey]);
  if (!upload) return null;
  const pct = upload.stage === "uploading" && upload.size > 0 ? Math.min(100, Math.round((upload.loaded / upload.size) * 100)) : null;
  const state = upload.stage === "failed" ? upload.error || STAGE.failed : pct !== null ? `${STAGE.uploading} ${pct}%` : STAGE[upload.stage];
  const label = `${upload.filename}${state ? `，${state}` : ""}`;
  return (
    <span className={`attach-chip att ${upload.stage}`} title={label}>
      <span className="att-tile">
        <ExtTile filename={upload.filename} size={22} />
        <Corner upload={upload} />
      </span>
      {onOpen && upload.stage === "ready" ? (
        <button type="button" className="att-name link" onClick={onOpen}>{upload.filename}</button>
      ) : (
        <span className="att-name">{upload.filename}</span>
      )}
      {upload.stage === "failed" ? <span className="chip-state att-error" role="status">{upload.error || "失败"}</span> : <span className="sr-only" role="status">{state}</span>}
      {upload.stage === "failed" && onRetry ? <button type="button" className="link att-retry" onClick={onRetry} aria-label={`重试 ${upload.filename}`}>重试</button> : null}
      {onRemove ? (
        <button type="button" className="link att-x" aria-label={`移除 ${upload.filename}`} onClick={onRemove}><IconX size={12} /></button>
      ) : null}
    </span>
  );
}
