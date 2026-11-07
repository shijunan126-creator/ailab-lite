"use client";

import { useEffect, useMemo, useState } from "react";
import { API_BASE, api, errorMessage, redirectIfUnauthorized, Skill, skillKind, type DraftFile } from "../../lib/api";
import { buildTree, type TreeNode } from "../../lib/draft-tree";
import { formatBytes } from "../../lib/format";
import { IconChevronRight, IconDownload, IconFile, IconFileText, IconFolder, IconFolderOpen, IconImage, IconRetry } from "../icons";
import Modal from "../library/modal";

type Listing = { status: "ready"; files: Omit<DraftFile, "change">[] } | { status: "preparing" } | { status: "failed"; error?: string };

const POLL_MS = 1000;
const WAIT_MS = 60_000;

const fileUrl = (name: string, path: string) =>
  `${API_BASE}/v1/skills/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;

/** A skill's folder, read only: every file the skill brings, SKILL.md first. A platform skill added from the
 * 技能广场 can be changed from here (that starts a personal draft); a built-in skill is only read. */
export default function SkillViewer({ skill, onClose, onCustomize }: { skill: Skill; onClose: () => void; onCustomize?: (skill: Skill) => void }) {
  const [files, setFiles] = useState<DraftFile[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState("SKILL.md");
  const [open, setOpen] = useState<Set<string>>(new Set()); // folders start closed: SKILL.md leads
  const [texts, setTexts] = useState<Record<string, string>>({});

  // The listing; a ZIP skill is unpacked once by the platform, so the first look may wait a moment.
  useEffect(() => {
    const ctl = new AbortController();
    const deadline = Date.now() + WAIT_MS;
    setError("");
    setFiles(null);
    const tick = async (): Promise<void> => {
      try {
        const listing = await api<Listing>(`/v1/skills/${encodeURIComponent(skill.name)}/files`, { signal: ctl.signal });
        if (ctl.signal.aborted) return;
        if (listing.status === "ready") return setFiles(listing.files.map((f) => ({ ...f, change: "unchanged" as const })));
        if (listing.status === "failed") return setError(listing.error || "没能读取技能文件");
        if (Date.now() > deadline) return setError("技能文件仍在准备，请稍后再看");
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        if (!ctl.signal.aborted) return tick();
      } catch (e) {
        if (!ctl.signal.aborted) setError(errorMessage(e));
      }
    };
    tick();
    return () => ctl.abort();
  }, [skill.name, attempt]);

  const tree = useMemo(() => buildTree(files || []), [files]);
  const current = files?.find((f) => f.path === selected) ?? files?.[0];
  const single = files?.length === 1;

  // Text of the selected file, loaded once.
  useEffect(() => {
    if (!current || current.kind !== "text" || texts[current.path] !== undefined) return;
    const ctl = new AbortController();
    fetch(fileUrl(skill.name, current.path), { signal: ctl.signal })
      .then(async (resp) => {
        redirectIfUnauthorized(resp);
        if (!resp.ok) throw new Error(`读取失败（${resp.status}）`);
        const text = await resp.text();
        if (!ctl.signal.aborted) setTexts((t) => ({ ...t, [current.path]: text }));
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setError(String(e?.message || e));
      });
    return () => ctl.abort();
  }, [skill.name, current, texts]);

  const customizable = !!onCustomize && skillKind(skill) === "platform";
  const footer = customizable ? (
    <>
      <span className="spacer" />
      <button className="sm" onClick={() => onCustomize!(skill)}>修改</button>
    </>
  ) : undefined;

  return (
    <Modal title={skill.title} onClose={onClose} className={`draft-editor skill-viewer ${single ? "single" : ""}`} footer={footer}>
      {error ? (
        <div className="draft-wait">
          <span>{error}</span>
          <button className="secondary sm" onClick={() => setAttempt((n) => n + 1)}><IconRetry size={15} /> 重试</button>
        </div>
      ) : !files ? (
        <div className="draft-wait" aria-live="polite"><span className="draft-spin" />加载中…</div>
      ) : (
        <div className="draft-body">
          {single ? null : (
            <div className="draft-side">
              <div className="draft-tree" role="tree" aria-label="技能文件">
                <Nodes
                  nodes={tree}
                  depth={0}
                  selected={current?.path ?? ""}
                  open={open}
                  onSelect={(n) => {
                    if (!n.dir) return setSelected(n.path);
                    setOpen((o) => { const next = new Set(o); if (next.has(n.path)) next.delete(n.path); else next.add(n.path); return next; });
                  }}
                />
              </div>
            </div>
          )}
          <div className="draft-main">
            {!current ? (
              <div className="empty">没有文件</div>
            ) : current.kind === "text" ? (
              texts[current.path] === undefined ? (
                <div className="empty">加载中…</div>
              ) : (
                <textarea key={current.path} className="skill-editor-text mono draft-text" aria-label={current.path} value={texts[current.path]} readOnly spellCheck={false} />
              )
            ) : (
              <div className="draft-binary">
                {current.kind === "image" ? <img src={fileUrl(skill.name, current.path)} alt={current.path} /> : <IconFile size={40} />}
                <div className="mono">{current.path}</div>
                <div className="hint">{formatBytes(current.size)}</div>
                <div className="draft-binary-actions">
                  <a className="btn secondary sm" href={fileUrl(skill.name, current.path)} download={current.path.split("/").pop()}>
                    <IconDownload size={15} /> 下载
                  </a>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function Nodes({ nodes, depth, selected, open: opened, onSelect }: { nodes: TreeNode[]; depth: number; selected: string; open: Set<string>; onSelect: (n: TreeNode) => void }) {
  return (
    <>
      {nodes.map((n) => {
        const open = n.dir && opened.has(n.path);
        const Icon = n.dir ? (open ? IconFolderOpen : IconFolder) : n.file?.kind === "image" ? IconImage : n.file?.kind === "text" ? IconFileText : IconFile;
        return (
          <div key={n.path} role="none">
            <div
              role="treeitem"
              aria-selected={!n.dir && n.path === selected}
              aria-expanded={n.dir ? open : undefined}
              tabIndex={0}
              className={`draft-node ${!n.dir && n.path === selected ? "on" : ""}`}
              style={{ paddingLeft: 8 + depth * 14 }}
              onClick={() => onSelect(n)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n); } }}
            >
              {n.dir ? <IconChevronRight size={12} className={`draft-caret ${open ? "open" : ""}`} /> : <span className="draft-caret" />}
              <Icon size={15} />
              <span className="draft-name">{n.name}</span>
            </div>
            {n.dir && open ? (
              <div role="group">
                <Nodes nodes={n.children || []} depth={depth + 1} selected={selected} open={opened} onSelect={onSelect} />
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}
