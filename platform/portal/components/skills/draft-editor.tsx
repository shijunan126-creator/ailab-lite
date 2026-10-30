"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, api, errorMessage, redirectIfUnauthorized, SkillDraft } from "../../lib/api";
import { confirmDialog } from "../../lib/dialog";
import { buildTree, joinPath, parentOf, pathProblem, TreeNode, within } from "../../lib/draft-tree";
import { draftLabel, draftsChanged, working } from "../../lib/skill-drafts";
import { formatBytes } from "../../lib/format";
import Modal from "../library/modal";
import {
  IconChevronRight,
  IconDownload,
  IconFile,
  IconFilePlus,
  IconFileText,
  IconFolder,
  IconFolderOpen,
  IconFolderPlus,
  IconImage,
  IconImageUp,
  IconPencil,
  IconRetry,
  IconTrash,
  IconUpload,
} from "../icons";

const POLL_MS = 2000;
const POLL_FOR_MS = 10 * 60_000;
const CHANGE: Record<string, string> = { added: "新增", modified: "修改", deleted: "删除" };

type Naming = { kind: "file" | "folder"; folder: string } | { kind: "rename"; node: TreeNode };

const fileUrl = (id: string, path: string) => `${API_BASE}/v1/skill-drafts/${id}/files/${path.split("/").map(encodeURIComponent).join("/")}`;

/** A skill draft as a folder: browse its files, edit text, replace images, add, rename and delete files and folders,
 * then save it as a skill. Nothing becomes a real skill until 保存; every write is checked against the draft's revision. */
export default function DraftEditor({
  draftId,
  onClose,
  onConfirmed,
}: {
  draftId: string;
  onClose: () => void;
  onConfirmed: (fileId: string, label: string) => void;
}) {
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [selected, setSelected] = useState<string>("SKILL.md");
  const [folder, setFolder] = useState(""); // where a new file or upload goes
  const [folders, setFolders] = useState<Set<string>>(new Set()); // empty folders made here
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [texts, setTexts] = useState<Record<string, string>>({}); // as loaded from the draft
  const [edits, setEdits] = useState<Record<string, string>>({}); // unsaved
  const [naming, setNaming] = useState<Naming | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const draftRef = useRef<SkillDraft | null>(null);
  const editsRef = useRef(edits);
  editsRef.current = edits;
  const saving = useRef(new Map<string, Promise<boolean>>()); // a blur and 保存 must not write the same file twice
  const writes = useRef(0);
  const mounted = useRef(true);
  const upload = useRef<HTMLInputElement | null>(null);
  const replaceInput = useRef<HTMLInputElement | null>(null);

  const accept = useCallback((next: SkillDraft) => {
    if (!mounted.current) return;
    // a write changed some files: forget what was loaded, paths may now hold something else or be gone
    if (draftRef.current && draftRef.current.revision !== next.revision) setTexts({});
    draftRef.current = next; // the next write needs this revision before React renders again
    setDraft(next);
  }, []);

  const reload = useCallback(async () => {
    const next = await api<SkillDraft>(`/v1/skill-drafts/${draftId}`);
    accept(next);
    return next;
  }, [draftId, accept]);

  // Load, and follow a draft that is still being made.
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const until = Date.now() + POLL_FOR_MS;
    const tick = async () => {
      try {
        const next = await reload();
        if (!stopped && working(next) && Date.now() < until) timer = setTimeout(tick, POLL_MS);
        else if (!working(next)) draftsChanged();
      } catch (e) {
        if (!stopped) setError(errorMessage(e));
      }
    };
    tick();
    return () => { stopped = true; mounted.current = false; clearTimeout(timer); };
  }, [reload]);

  const files = useMemo(() => draft?.files || [], [draft]);
  const tree = useMemo(() => buildTree(files, folders), [files, folders]);
  const current = files.find((f) => f.path === selected && f.change !== "deleted");
  const ready = draft?.status === "ready";
  const dirty = Object.keys(edits).filter((p) => edits[p] !== texts[p] && files.some((f) => f.path === p && f.change !== "deleted"));

  // Text of the selected file, loaded once per revision.
  useEffect(() => {
    if (!draft || !ready || !current || current.kind !== "text" || texts[current.path] !== undefined) return;
    const ctl = new AbortController();
    fetch(fileUrl(draft.id, current.path), { signal: ctl.signal, cache: "no-store" })
      .then(async (resp) => {
        redirectIfUnauthorized(resp);
        if (!resp.ok) throw new Error(`读取失败（${resp.status}）`);
        const text = await resp.text();
        if (!ctl.signal.aborted) setTexts((t) => ({ ...t, [current.path]: text }));
      })
      .catch((e) => { if (!ctl.signal.aborted) setError(String(e?.message || e)); });
    return () => ctl.abort();
  }, [draft, ready, current, texts]);

  /** One write to the draft. A 409 means someone (another tab) changed it: show the newer draft, keep unsaved text.
   * Saving text does not lock the editor: a blur starts one just as a click lands on 保存, and a button disabled
   * between mousedown and mouseup swallows that click. confirm() and move wait for it through flush(). */
  async function write<T = SkillDraft>(path: string, init: RequestInit, lock = true): Promise<T | null> {
    if (lock) {
      writes.current++;
      setBusy(true);
    }
    setError("");
    try {
      return await api<T>(path, init);
    } catch (e) {
      setError(errorMessage(e));
      if (String((e as Error)?.message || "").startsWith("409")) await reload().catch(() => undefined);
      return null;
    } finally {
      if (lock && --writes.current === 0 && mounted.current) setBusy(false);
    }
  }

  const revision = () => draftRef.current!.revision;

  function saveText(path: string): Promise<boolean> {
    const running = saving.current.get(path);
    if (running) return running;
    const content = editsRef.current[path];
    if (content === undefined) return Promise.resolve(true);
    const job = (async () => {
      const next = await write(`/v1/skill-drafts/${draftId}/files/${path.split("/").map(encodeURIComponent).join("/")}`, {
        method: "PUT",
        body: JSON.stringify({ content, expected_revision: revision() }),
      }, false);
      if (!next) return false;
      accept(next);
      setTexts((t) => ({ ...t, [path]: content }));
      const rest = { ...editsRef.current };
      if (rest[path] === content) delete rest[path];
      editsRef.current = rest;
      setEdits(rest);
      return true;
    })().finally(() => saving.current.delete(path));
    saving.current.set(path, job);
    return job;
  }

  async function putFile(path: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    form.append("expected_revision", String(revision()));
    const next = await write(`/v1/skill-drafts/${draftId}/files/${path.split("/").map(encodeURIComponent).join("/")}`, { method: "PUT", body: form });
    if (next) accept(next);
    return !!next;
  }

  async function uploadFiles(list: FileList) {
    for (const file of Array.from(list)) {
      const path = joinPath(folder, file.name);
      if (pathProblem(path)) { setError(`${file.name}：${pathProblem(path)}`); continue; }
      if (!(await putFile(path, file))) break;
      setSelected(path);
    }
    setFolders((f) => { const next = new Set(f); next.delete(folder); return next; });
  }

  async function create(kind: "file" | "folder", base: string, name: string) {
    const path = joinPath(base, name.trim());
    const problem = pathProblem(path);
    if (problem) { setError(problem); return; }
    if (files.some((f) => f.change !== "deleted" && (f.path === path || within(f.path, path)))) { setError("已有同名文件或文件夹"); return; }
    setNaming(null);
    if (kind === "folder") {
      setFolders((f) => new Set(f).add(path));
      setFolder(path);
      return;
    }
    const next = await write(`/v1/skill-drafts/${draftId}/files/${path.split("/").map(encodeURIComponent).join("/")}`, {
      method: "PUT",
      body: JSON.stringify({ content: "", expected_revision: revision() }),
    });
    if (!next) return;
    accept(next);
    setTexts((t) => ({ ...t, [path]: "" }));
    setSelected(path);
    setFolders((f) => { const n = new Set(f); for (const p of f) if (within(path, p)) n.delete(p); return n; });
  }

  async function rename(node: TreeNode, name: string) {
    const to = joinPath(parentOf(node.path), name.trim());
    const problem = pathProblem(to);
    if (problem) { setError(problem); return; }
    setNaming(null);
    if (to === node.path) return;
    if (node.dir && !files.some((f) => within(f.path, node.path) && f.change !== "deleted")) {
      setFolders((f) => { const n = new Set<string>(); for (const p of f) n.add(within(p, node.path) ? to + p.slice(node.path.length) : p); return n; });
      return;
    }
    if (!(await flush())) return;
    const next = await write(`/v1/skill-drafts/${draftId}/move`, { method: "POST", body: JSON.stringify({ from: node.path, to, expected_revision: revision() }) });
    if (!next) return;
    accept(next);
    if (within(selected, node.path)) setSelected(to + selected.slice(node.path.length));
    if (within(folder, node.path)) setFolder(to + folder.slice(node.path.length));
  }

  async function remove(node: TreeNode) {
    if (node.path === "SKILL.md") { setError("SKILL.md 不能删除"); return; }
    if (node.dir && !(await confirmDialog({ title: `删除文件夹「${node.name}」？`, body: "其中的文件会一起删除。", action: "删除", danger: true }))) return;
    if (node.dir && !files.some((f) => within(f.path, node.path) && f.change !== "deleted")) {
      setFolders((f) => { const n = new Set(f); for (const p of f) if (within(p, node.path)) n.delete(p); return n; });
      if (within(folder, node.path)) setFolder(parentOf(node.path));
      return;
    }
    const next = await write(`/v1/skill-drafts/${draftId}/files/${node.path.split("/").map(encodeURIComponent).join("/")}?expected_revision=${revision()}`, { method: "DELETE" });
    if (!next) return;
    accept(next);
    setEdits((e) => Object.fromEntries(Object.entries(e).filter(([p]) => !within(p, node.path))));
    if (within(selected, node.path)) setSelected("SKILL.md");
    if (within(folder, node.path)) setFolder(parentOf(node.path));
  }

  /** Save every edited text file; false when one of them could not be saved. */
  async function flush(): Promise<boolean> {
    await Promise.all(saving.current.values());
    for (const path of Object.keys(editsRef.current)) if (!(await saveText(path))) return false;
    return true;
  }

  async function confirm() {
    if (!draft || !ready || writes.current) return; // the ref, not busy: a second click can land before the next render
    writes.current++;
    setBusy(true);
    try {
      if (!(await flush())) return;
      const saved = await write<{ file_id: string; name: string }>(`/v1/skill-drafts/${draftId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: revision() }),
      });
      if (!saved) return;
      draftsChanged();
      onConfirmed(saved.file_id, draftRef.current?.title || saved.name);
    } finally {
      if (--writes.current === 0 && mounted.current) setBusy(false);
    }
  }

  async function retry() {
    const next = await write(`/v1/skill-drafts/${draftId}/retry?expected_revision=${revision()}`, { method: "POST" });
    if (!next) return;
    accept(next);
    draftsChanged();
    // follow it again
    const until = Date.now() + POLL_FOR_MS;
    const tick = async () => {
      if (!mounted.current || Date.now() > until) return;
      const latest = await reload().catch(() => null);
      if (latest && working(latest)) setTimeout(tick, POLL_MS);
      else draftsChanged();
    };
    setTimeout(tick, POLL_MS);
  }

  async function discard() {
    if (!draft || !(await confirmDialog({ title: "丢弃这个草稿？", body: "已有的技能不受影响。", action: "丢弃", danger: true }))) return;
    const done = await write(`/v1/skill-drafts/${draftId}?expected_revision=${revision()}`, { method: "DELETE" });
    if (done === null) return;
    draftsChanged();
    onClose();
  }

  const close = useCallback(async () => {
    if (busy) return;
    await Promise.all(saving.current.values()); // a blur may have just started saving what would otherwise count as unsaved
    if (Object.keys(editsRef.current).length && !(await confirmDialog({ title: "放弃未保存的修改？", action: "放弃", danger: true }))) return;
    onClose();
  }, [busy, onClose]);

  const title = draft?.title || draft?.name || (draft?.base ? draft.base.name : "技能草稿");
  const footer = draft && (ready || draft.status === "failed") ? (
    <>
      <button className="link danger-text" onClick={discard} disabled={busy}>丢弃草稿</button>
      <span className="spacer" />
      <button className="secondary sm" onClick={close} disabled={busy}>关闭</button>
      {ready ? (
        <button className="sm" onClick={confirm} disabled={busy}>
          {draft.action === "update" ? "保存新版本" : "保存为技能"}
        </button>
      ) : (
        <button className="sm" onClick={retry} disabled={busy}><IconRetry size={15} /> 重试</button>
      )}
    </>
  ) : undefined;

  return (
    <Modal title={title} onClose={close} className="draft-editor" footer={footer}>
      {error ? <div className="alert error skill-editor-alert">{error}</div> : null}
      {!draft ? (
        error ? null : <div className="empty">加载中…</div>
      ) : working(draft) ? (
        <div className="draft-wait" aria-live="polite"><span className="draft-spin" />{draftLabel(draft)}</div>
      ) : draft.status === "failed" ? (
        <div className="draft-wait">{draft.error || "提取失败"}</div>
      ) : (
        <>
          {draft.summary ? <p className="draft-summary">{draft.summary}</p> : null}
          <div className="draft-body">
            <div className="draft-side">
              <div className="draft-tools">
                <button className="icon-btn" title="新建文件" aria-label="新建文件" disabled={busy} onClick={() => setNaming({ kind: "file", folder })}><IconFilePlus size={16} /></button>
                <button className="icon-btn" title="新建文件夹" aria-label="新建文件夹" disabled={busy} onClick={() => setNaming({ kind: "folder", folder })}><IconFolderPlus size={16} /></button>
                <button className="icon-btn" title="上传文件" aria-label="上传文件" disabled={busy} onClick={() => upload.current?.click()}><IconUpload size={16} /></button>
                <span className="draft-target hint" title={folder || "根目录"}>{folder ? `${folder}/` : ""}</span>
                <input ref={upload} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files); e.target.value = ""; }} />
              </div>
              <div className="draft-tree" role="tree" aria-label="技能文件">
                {naming && naming.kind !== "rename" && !naming.folder ? (
                  <NameInput depth={0} kind={naming.kind} onDone={(name) => (name === null ? setNaming(null) : create(naming.kind, "", name))} />
                ) : null}
                <Nodes
                  nodes={tree}
                  depth={0}
                  selected={selected}
                  folder={folder}
                  closed={closed}
                  dirty={dirty}
                  naming={naming}
                  busy={busy}
                  onSelect={(n) => {
                    if (n.dir) {
                      setFolder(n.path);
                      setClosed((c) => { const next = new Set(c); if (next.has(n.path)) next.delete(n.path); else next.add(n.path); return next; });
                    } else {
                      setSelected(n.path);
                      setFolder(parentOf(n.path));
                    }
                  }}
                  onRename={(n) => setNaming({ kind: "rename", node: n })}
                  onRemove={remove}
                  onNamed={(name) => {
                    if (!naming || name === null) return setNaming(null);
                    if (naming.kind === "rename") rename(naming.node, name);
                    else create(naming.kind, naming.folder, name);
                  }}
                />
              </div>
            </div>
            <div className="draft-main">
              {!current ? (
                <div className="empty">选择一个文件</div>
              ) : current.kind === "text" ? (
                texts[current.path] === undefined ? (
                  <div className="empty">加载中…</div>
                ) : (
                  <textarea
                    key={current.path}
                    className="skill-editor-text mono draft-text"
                    aria-label={current.path}
                    value={edits[current.path] ?? texts[current.path]}
                    spellCheck={false}
                    readOnly={busy}
                    onChange={(e) => { const value = e.target.value; setEdits((x) => ({ ...x, [current.path]: value })); }}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
                        e.preventDefault();
                        saveText(current.path);
                      }
                    }}
                    onBlur={() => { if (dirty.includes(current.path)) saveText(current.path); }}
                  />
                )
              ) : (
                <div className="draft-binary">
                  {current.kind === "image" ? <img src={`${fileUrl(draft.id, current.path)}?r=${draft.revision}`} alt={current.path} /> : <IconFile size={40} />}
                  <div className="mono">{current.path}</div>
                  <div className="hint">{formatBytes(current.size)}</div>
                  <div className="draft-binary-actions">
                    <a className="btn secondary sm" href={fileUrl(draft.id, current.path)} download={current.path.split("/").pop()}>
                      <IconDownload size={15} /> 下载
                    </a>
                    <button className="secondary sm" disabled={busy} onClick={() => replaceInput.current?.click()}>
                      {current.kind === "image" ? <IconImageUp size={15} /> : <IconUpload size={15} />} 替换
                    </button>
                    <input
                      ref={replaceInput}
                      type="file"
                      accept={current.kind === "image" ? "image/png,image/jpeg,image/gif,image/webp" : undefined}
                      hidden
                      onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) putFile(current.path, f); }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </Modal>
  );
}

function Nodes(props: {
  nodes: TreeNode[];
  depth: number;
  selected: string;
  folder: string;
  closed: Set<string>;
  dirty: string[];
  naming: Naming | null;
  busy: boolean;
  onSelect: (n: TreeNode) => void;
  onRename: (n: TreeNode) => void;
  onRemove: (n: TreeNode) => void;
  onNamed: (name: string | null) => void;
}) {
  const { nodes, depth, selected, folder, closed, dirty, naming, busy } = props;
  return (
    <>
      {nodes.map((n) => {
        const open = n.dir && !closed.has(n.path);
        const gone = n.change === "deleted";
        const renaming = naming?.kind === "rename" && naming.node.path === n.path;
        const Icon = n.dir ? (open ? IconFolderOpen : IconFolder) : n.file?.kind === "image" ? IconImage : n.file?.kind === "text" ? IconFileText : IconFile;
        return (
          <div key={n.path} role="none">
            {renaming ? (
              <NameInput depth={depth} kind={n.dir ? "folder" : "file"} initial={n.name} onDone={props.onNamed} />
            ) : (
              <div
                role="treeitem"
                aria-selected={n.dir ? n.path === folder : n.path === selected}
                aria-expanded={n.dir ? open : undefined}
                tabIndex={0}
                className={`draft-node ${(!n.dir && n.path === selected) || (n.dir && n.path === folder) ? "on" : ""} ${gone ? "gone" : ""}`}
                style={{ paddingLeft: 8 + depth * 14 }}
                onClick={() => !gone && props.onSelect(n)}
                onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !gone) { e.preventDefault(); props.onSelect(n); } }}
              >
                {n.dir ? <IconChevronRight size={12} className={`draft-caret ${open ? "open" : ""}`} /> : <span className="draft-caret" />}
                <Icon size={15} />
                <span className="draft-name">{n.name}{!n.dir && dirty.includes(n.path) ? " •" : ""}</span>
                {n.change && n.change !== "unchanged" ? <span className={`draft-change ${n.change}`}>{CHANGE[n.change]}</span> : null}
                {!gone && n.path !== "SKILL.md" ? (
                  <span className="draft-node-actions">
                    <button className="icon-btn" aria-label={`重命名 ${n.name}`} title="重命名" disabled={busy} onClick={(e) => { e.stopPropagation(); props.onRename(n); }}><IconPencil size={13} /></button>
                    <button className="icon-btn" aria-label={`删除 ${n.name}`} title="删除" disabled={busy} onClick={(e) => { e.stopPropagation(); props.onRemove(n); }}><IconTrash size={13} /></button>
                  </span>
                ) : null}
              </div>
            )}
            {n.dir && open ? (
              <div role="group">
                {naming && naming.kind !== "rename" && naming.folder === n.path ? <NameInput depth={depth + 1} kind={naming.kind} onDone={props.onNamed} /> : null}
                <Nodes {...props} nodes={n.children || []} depth={depth + 1} />
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}

function NameInput({ depth, kind, initial = "", onDone }: { depth: number; kind: "file" | "folder"; initial?: string; onDone: (name: string | null) => void }) {
  const [value, setValue] = useState(initial);
  const done = useRef(false);
  const finish = (name: string | null) => {
    if (done.current) return;
    done.current = true;
    onDone(name);
  };
  return (
    <div className="draft-node naming" style={{ paddingLeft: 8 + depth * 14 }}>
      <span className="draft-caret" />
      {kind === "folder" ? <IconFolder size={15} /> : <IconFileText size={15} />}
      <input
        autoFocus
        value={value}
        aria-label={kind === "folder" ? "文件夹名" : "文件名"}
        placeholder={kind === "folder" ? "文件夹名" : "文件名"}
        onChange={(e) => setValue(e.target.value)}
        onFocus={(e) => { const dot = initial.lastIndexOf("."); e.target.setSelectionRange(0, dot > 0 ? dot : initial.length); }}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); finish(value.trim() || null); }
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
        }}
        onBlur={() => finish(value.trim() && value.trim() !== initial ? value.trim() : null)}
      />
    </div>
  );
}
