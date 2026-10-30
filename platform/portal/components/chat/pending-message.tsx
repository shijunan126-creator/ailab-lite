"use client";

import { discard, retry, type Pending } from "../../lib/outbox";
import { canRetry, retryUpload } from "../../lib/uploads";
import { fromPayload } from "../../lib/message-parts";
import AttachmentChip from "./attachment-chip";
import MessageText from "./message-text";

/** A sent message that is not a turn yet: its files are still uploading or being checked, or it failed to go out.
 * It sits where the turn will be; nothing here suggests the model is already answering (HLD §16.2–16.3). */
export default function PendingMessage({ message }: { message: Pending }) {
  const failed = message.stage === "failed";
  return (
    <div className={`turn pending-turn ${failed ? "failed" : ""}`} data-stage={message.stage}>
      <div className="msg user">
        <div className="bubble"><MessageText parts={fromPayload(message.prompt, message.options.skills)} /></div>
        {message.attachments.length ? (
          <div className="attach-row">
            {message.attachments.map((key) => (
              <AttachmentChip key={key} uploadKey={key} onRetry={failed && canRetry(key) ? () => retryUpload(key, message.projectId) : undefined} />
            ))}
          </div>
        ) : null}
        {failed ? (
          <div className="send-failed" role="alert">
            <span>发送失败{message.error ? `：${message.error}` : ""}</span>
            <button type="button" className="link" onClick={() => retry(message.id)}>重试</button>
            <button type="button" className="link muted" onClick={() => discard(message.id)}>删除</button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
