"use client";

import { DragEvent, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  IconChevronRight,
  IconDownload,
  IconFolder,
  IconFolderInput,
  IconFolderPlus,
  IconMessage,
  IconMore,
  IconRestore,
  IconSearch,
  IconStar,
  IconTrash,
  IconUpload,
  IconX,
  IconCheck,
} from "../../../components/icons";
import FileTile, { thumbUrl } from "../../../components/library/file-tile";
import { MoveDialog, NewProjectInput } from "../../../components/library/projects";
import FileViewer from "../../../components/preview/file-viewer";
import { Category, useLibrary } from "../../../components/library/use-library";
import { api, archiveUrl, downloadUrl, FileItem, Info, MAX_ATTACHMENTS, Project, uploadFile } from "../../../lib/api";
import { formatBytes, timeAgo } from "../../../lib/format";
import { projectsChanged, useProjects } from "../../../lib/projects";

type Tab = Category | "projects";
const BATCH = 200; // the most files one request may touch
const TABS: [Tab, string][] = [
  ["all", "全部"],
  ["starred", "收藏"],
  ["images", "图像"],
  ["projects", "项目"],
  ["trash", "回收站"],
];
const DAY = 86_400_000;

function purgeText(file: FileItem): string {
  const days = file.purge_at ? Math.max(0, Math.ceil((new Date(file.purge_at).getTime() - Date.now()) / DAY)) : null;
  return days === null ? "" : days === 0 ? "今天永久删除" : `${days} 天后永久删除`;
}

function statusText(file: FileItem): string | null {
  if (file.status === "rejected") return "已拒绝";
  if (file.status === "pending") return file.meta?.processing === "error" ? "扫描出错" : "扫描中";
  return null;
}

/** Where "talk about these" opens: their project if they share one, else a plain new chat. */
function chatHref(files: FileItem[]): string {
  const ids = files.map((f) => f.id).join(",");
  const projects = new Set(files.map((f) => f.project_id || ""));
  const [only] = [...projects];
  return projects.size === 1 && only ? `/p/${only}?files=${ids}` : `/?files=${ids}`;
}

function Library() {
  const router = useRouter();
  const params = useSearchParams();
  const projectId = params.get("project");
  const tab: Tab = projectId ? "projects" : ((TABS.find(([k]) => k === params.get("c"))?.[0] as Tab) || "all");
  const projects = useProjects();
  const project = projectId ? projects?.find((p) => p.id === projectId) : null;

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [moving, setMoving] = useState<FileItem[] | null>(null);
  const [viewing, setViewing] = useState<FileItem | null>(null);
  const [addingProject, setAddingProject] = useState(false);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [notice, setNotice] = useState("");
  const [maxUpload, setMaxUpload] = useState(0);
  const [undo, setUndo] = useState<FileItem[] | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const showsFiles = tab !== "projects" || !!projectId;
  const trash = tab === "trash";
  const canUpload = showsFiles && !trash;
  const { files, setFiles, loaded, more, error, reload, loadMore } = useLibrary(
    { category: tab === "projects" ? "all" : tab, projectId, q },
    showsFiles
  );

  useEffect(() => {
    api<Info>("/v1/info").then((i) => setMaxUpload(i.max_upload_bytes)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQ(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  // a selection belongs to one view
  useEffect(() => setSelected(new Set()), [tab, projectId, q]);

  useEffect(() => {
    if (!menuFor) return;
    const close = (e: MouseEvent) => !(e.target as HTMLElement).closest?.(".row-menu") && setMenuFor(null);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuFor]);

  const chosen = useMemo(() => files.filter((f) => selected.has(f.id)), [files, selected]);
  const projectName = (id?: string | null) => (id ? projects?.find((p) => p.id === id)?.name : undefined);

  function go(next: Tab) {
    setSearch("");
    router.push(next === "all" ? "/files" : `/files?c=${next}`);
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  async function run(work: () => Promise<unknown>, after?: () => void) {
    setNotice("");
    try {
      await work();
      after?.();
    } catch (err: any) {
      setNotice(String(err?.message || err));
    } finally {
      reload();
    }
  }

  function batch(action: "star" | "unstar" | "move" | "trash" | "restore" | "purge", list: FileItem[], extra: Record<string, unknown> = {}) {
    if (list.length > BATCH) return Promise.reject(new Error(`一次最多处理 ${BATCH} 个文件`));
    return api<{ count: number }>("/v1/files/batch", { method: "POST", body: JSON.stringify({ action, ids: list.map((f) => f.id), ...extra }) });
  }

  function star(list: FileItem[], on: boolean) {
    const ids = new Set(list.map((f) => f.id));
    setFiles((prev) => prev.map((f) => (ids.has(f.id) ? { ...f, starred: on } : f))); // the star answers at once
    run(() => batch(on ? "star" : "unstar", list));
  }

  // Deleting only moves to the trash, so it asks nothing and offers an undo instead.
  function remove(list: FileItem[]) {
    run(
      () => batch("trash", list),
      () => {
        setSelected(new Set());
        setUndo(list);
        projectsChanged();
      }
    );
  }

  function restore(list: FileItem[]) {
    setUndo(null);
    run(
      () => batch("restore", list),
      () => {
        setSelected(new Set());
        projectsChanged();
      }
    );
  }

  function purge(list: FileItem[]) {
    const label = list.length === 1 ? `「${list[0].filename}」` : `这 ${list.length} 个文件`;
    if (!confirm(`永久删除${label}及其所有版本？无法恢复。`)) return;
    run(() => batch("purge", list), () => setSelected(new Set()));
  }

  function emptyTrash() {
    if (!confirm("清空回收站？其中的文件将永久删除，无法恢复。")) return;
    run(() => api("/v1/trash", { method: "DELETE" }));
  }

  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(null), 6000);
    return () => clearTimeout(timer);
  }, [undo]);

  function move(list: FileItem[], target: string | null) {
    setMoving(null);
    run(
      () => batch("move", list, { project_id: target }),
      () => {
        setSelected(new Set());
        projectsChanged();
      }
    );
  }

  function download(list: FileItem[]) {
    const ready = list.filter((f) => f.status === "ready");
    if (!ready.length) return;
    if (ready.length > BATCH) return setNotice(`一次最多下载 ${BATCH} 个文件`);
    window.location.href = ready.length === 1 ? downloadUrl(ready[0].download_url) : archiveUrl(ready.map((f) => f.id));
  }

  async function upload(list: FileList | File[]) {
    setBusy(true);
    setNotice("");
    try {
      for (const file of Array.from(list)) await uploadFile(file, projectId);
      if (projectId) projectsChanged();
    } catch (err: any) {
      setNotice(String(err?.message || err));
    } finally {
      if (inputRef.current) inputRef.current.value = "";
      setBusy(false);
      reload();
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    if (!busy && canUpload && event.dataTransfer.files.length) upload(event.dataTransfer.files);
  }

  const allOn = files.length > 0 && files.every((f) => selected.has(f.id));
  const allStarred = chosen.length > 0 && chosen.every((f) => f.starred);
  const readyChosen = chosen.filter((f) => f.status === "ready");

  const trashActions = (f: FileItem) => (
    <div className="row-actions">
      <button className="icon-btn" onClick={() => restore([f])} title="恢复" aria-label={`恢复 ${f.filename}`}>
        <IconRestore size={16} />
      </button>
      <button className="icon-btn danger-hover" onClick={() => purge([f])} title="永久删除" aria-label={`永久删除 ${f.filename}`}>
        <IconTrash size={16} />
      </button>
    </div>
  );

  const rowActions = (f: FileItem) => (
    <div className="row-actions">
      {f.status === "ready" ? (
        <>
          <a className="icon-btn" href={chatHref([f])} title="围绕它对话" aria-label={`围绕 ${f.filename} 对话`}>
            <IconMessage size={16} />
          </a>
          <a className="icon-btn" href={downloadUrl(f.download_url)} title="下载" aria-label={`下载 ${f.filename}`}>
            <IconDownload size={16} />
          </a>
        </>
      ) : null}
      <div className="row-menu">
        <button className="icon-btn" onClick={() => setMenuFor(menuFor === f.id ? null : f.id)} aria-label={`更多操作 ${f.filename}`} aria-expanded={menuFor === f.id}>
          <IconMore size={16} />
        </button>
        {menuFor === f.id ? (
          <div className="pop-menu right" role="menu">
            <button
              className="menu-item"
              role="menuitem"
              onClick={() => {
                setMenuFor(null);
                setMoving([f]);
              }}
            >
              <IconFolderInput size={15} /> 移动到项目
            </button>
            <button
              className="menu-item danger"
              role="menuitem"
              onClick={() => {
                setMenuFor(null);
                remove([f]);
              }}
            >
              <IconTrash size={15} /> 删除
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );

  const starButton = (f: FileItem) => (
    <button
      className={`star-btn ${f.starred ? "on" : ""}`}
      onClick={() => star([f], !f.starred)}
      aria-pressed={!!f.starred}
      aria-label={f.starred ? `取消收藏 ${f.filename}` : `收藏 ${f.filename}`}
      title={f.starred ? "取消收藏" : "收藏"}
    >
      <IconStar size={16} filled={f.starred} />
    </button>
  );

  const checkbox = (f: FileItem) => (
    <button
      className={`check ${selected.has(f.id) ? "on" : ""}`}
      onClick={() => toggle(f.id)}
      role="checkbox"
      aria-checked={selected.has(f.id)}
      aria-label={`选择 ${f.filename}`}
    >
      {selected.has(f.id) ? <IconCheck size={12} /> : null}
    </button>
  );

  const empty =
    q ? (
      <div className="empty">没有匹配「{q}」的文件</div>
    ) : trash ? (
      <div className="empty">
        <strong>回收站是空的</strong>
      </div>
    ) : tab === "starred" ? (
      <div className="empty">
        <strong>还没有收藏</strong>点文件旁的星标即可收藏。
      </div>
    ) : tab === "images" ? (
      <div className="empty">
        <strong>还没有图像</strong>
      </div>
    ) : (
      <div className="empty">
        <strong>{projectId ? "这个项目还没有文件" : "还没有文件"}</strong>
        拖到这里，或点右上角上传。
      </div>
    );

  return (
    <main
      className={`page page-library ${over ? "drag-over" : ""}`}
      onDragOver={(e) => {
        if (!canUpload || !e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setOver(true);
      }}
      onDrop={onDrop}
    >
      <div className="library-head">
        <h1>资料库</h1>
        <span className="spacer" />
        {trash ? (
          files.length ? (
            <button className="secondary" onClick={emptyTrash}>
              <IconTrash size={16} /> 清空回收站
            </button>
          ) : null
        ) : showsFiles ? (
          <button
            className="secondary"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            title={maxUpload ? `单个文件最大 ${formatBytes(maxUpload)}` : undefined}
          >
            <IconUpload size={16} /> {busy ? "上传中…" : "上传"}
          </button>
        ) : null}
        <input ref={inputRef} type="file" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
      </div>

      <div className="library-bar">
        <div className="tabs" role="tablist">
          {TABS.map(([key, label]) => (
            <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => go(key)}>
              {label}
            </button>
          ))}
        </div>
        {showsFiles ? (
          <label className="search">
            <IconSearch size={15} />
            <input type="text" placeholder="搜索文件名" aria-label="搜索文件" value={search} onChange={(e) => setSearch(e.target.value)} />
            {search ? (
              <button className="link" onClick={() => setSearch("")} aria-label="清除搜索">
                <IconX size={13} />
              </button>
            ) : null}
          </label>
        ) : null}
      </div>

      {projectId ? (
        <div className="library-crumb">
          <a href="/files?c=projects">项目</a>
          <IconChevronRight size={14} />
          <span>{project?.name || "…"}</span>
          <span className="spacer" />
          <a href={`/p/${projectId}`}>打开项目</a>
        </div>
      ) : null}

      {error || notice ? <div className="alert error library-alert">{notice || error}</div> : null}

      {!showsFiles ? (
        <div className="project-grid">
          {(projects || []).map((p: Project) => (
            <a key={p.id} className="project-card" href={`/files?project=${p.id}`}>
              <span className="project-mark">
                <IconFolder size={18} />
              </span>
              <span className="project-card-name">{p.name}</span>
              <span className="meta">
                {p.files} 个文件 · {p.conversations} 个对话
              </span>
            </a>
          ))}
          {addingProject ? (
            <div className="project-card adding">
              <NewProjectInput
                onCreated={(p) => {
                  setAddingProject(false);
                  router.push(`/files?project=${p.id}`);
                }}
                onCancel={() => setAddingProject(false)}
              />
            </div>
          ) : (
            <button className="project-card new" onClick={() => setAddingProject(true)}>
              <span className="project-mark">
                <IconFolderPlus size={18} />
              </span>
              <span className="project-card-name">新建项目</span>
            </button>
          )}
        </div>
      ) : !loaded ? (
        <div className="empty">加载中…</div>
      ) : files.length === 0 ? (
        empty
      ) : tab === "images" ? (
        <div className={`image-grid ${selected.size ? "selecting" : ""}`}>
          {files.map((f) => (
            <div key={f.id} className={`image-card ${selected.has(f.id) ? "on" : ""} ${menuFor === f.id ? "menu-open" : ""}`}>
              <button className="image-thumb" onClick={() => (selected.size ? toggle(f.id) : setViewing(f))} aria-label={f.filename}>
                {thumbUrl(f) ? <img src={thumbUrl(f)!} alt="" loading="lazy" decoding="async" /> : <FileTile file={f} size={44} />}
                {statusText(f) ? <span className="status queued">{statusText(f)}</span> : null}
              </button>
              {checkbox(f)}
              {starButton(f)}
              <div className="image-foot">
                <span className="name" title={f.filename}>
                  {f.filename}
                </span>
                {rowActions(f)}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className={`file-list ${selected.size ? "selecting" : ""}`}>
          <div className="file-list-head">
            <button
              className={`check ${allOn ? "on" : selected.size ? "some" : ""}`}
              role="checkbox"
              aria-checked={allOn ? true : selected.size ? "mixed" : false}
              aria-label="全选"
              onClick={() => setSelected(allOn ? new Set() : new Set(files.map((f) => f.id)))}
            >
              {allOn ? <IconCheck size={12} /> : selected.size ? <span className="dash" /> : null}
            </button>
            <span>名称</span>
          </div>
          {files.map((f) => {
            const st = statusText(f);
            const inProject = !projectId ? projectName(f.project_id) : undefined;
            const open = () => (selected.size || trash ? toggle(f.id) : setViewing(f)); // the trash only selects
            return (
              <div key={f.id} className={`lib-row ${selected.has(f.id) ? "on" : ""}`}>
                {checkbox(f)}
                <button className="lib-open" onClick={open} aria-label={trash ? `选择 ${f.filename}` : `预览 ${f.filename}`}>
                  <FileTile file={f} size={36} />
                </button>
                <div className="grow">
                  <button className="name" title={f.filename} onClick={open}>
                    {f.filename}
                  </button>
                  <div className="meta">
                    {formatBytes(f.size)} · {trash ? purgeText(f) : timeAgo(f.created_at)}
                    {(f.versions || 0) > 1 ? ` · ${f.versions} 个版本` : ""}
                    {inProject ? (
                      <a className="meta-project" href={`/files?project=${f.project_id}`}>
                        <IconFolder size={12} /> {inProject}
                      </a>
                    ) : null}
                    {f.meta?.skill ? (
                      <a className="meta-project" href="/skills">
                        个人技能 · {f.meta.skill.title}
                      </a>
                    ) : null}
                  </div>
                </div>
                {st && !trash ? <span className={`status ${f.status === "rejected" ? "failed" : "queued"}`}>{st}</span> : null}
                {trash ? trashActions(f) : starButton(f)}
                {trash ? null : rowActions(f)}
              </div>
            );
          })}
        </div>
      )}

      {showsFiles && more ? (
        <div className="load-more">
          <button className="link" onClick={loadMore}>
            加载更多
          </button>
        </div>
      ) : null}

      {chosen.length && trash ? (
        <div className="select-bar" role="toolbar" aria-label="批量操作">
          <span className="select-count">已选 {chosen.length} 项</span>
          <span className="select-sep" />
          <button className="select-action" onClick={() => restore(chosen)}>
            <IconRestore size={15} /> 恢复
          </button>
          <button className="select-action danger" onClick={() => purge(chosen)}>
            <IconTrash size={15} /> 永久删除
          </button>
          <span className="select-sep" />
          <button className="select-action icon" onClick={() => setSelected(new Set())} aria-label="取消选择" title="取消选择">
            <IconX size={15} />
          </button>
        </div>
      ) : chosen.length ? (
        <div className="select-bar" role="toolbar" aria-label="批量操作">
          <span className="select-count">已选 {chosen.length} 项</span>
          <span className="select-sep" />
          <a
            className={`select-action ${readyChosen.length && readyChosen.length <= MAX_ATTACHMENTS ? "" : "disabled"}`}
            href={readyChosen.length && readyChosen.length <= MAX_ATTACHMENTS ? chatHref(readyChosen) : undefined}
            aria-disabled={!readyChosen.length || readyChosen.length > MAX_ATTACHMENTS}
            title={readyChosen.length > MAX_ATTACHMENTS ? `一条消息最多带 ${MAX_ATTACHMENTS} 个文件，已选 ${readyChosen.length} 个` : undefined}
          >
            <IconMessage size={15} /> 对话
          </a>
          <button className="select-action" disabled={!readyChosen.length} onClick={() => download(chosen)}>
            <IconDownload size={15} /> 下载
          </button>
          <button className="select-action" onClick={() => star(chosen, !allStarred)}>
            <IconStar size={15} filled={allStarred} /> {allStarred ? "取消收藏" : "收藏"}
          </button>
          <button className="select-action" onClick={() => setMoving(chosen)}>
            <IconFolderInput size={15} /> 移动
          </button>
          <button className="select-action danger" onClick={() => remove(chosen)}>
            <IconTrash size={15} /> 删除
          </button>
          <span className="select-sep" />
          <button className="select-action icon" onClick={() => setSelected(new Set())} aria-label="取消选择" title="取消选择">
            <IconX size={15} />
          </button>
        </div>
      ) : null}

      {moving ? (
        <MoveDialog
          title={moving.length === 1 ? `移动「${moving[0].filename}」` : `移动 ${moving.length} 个文件`}
          current={new Set(moving.map((f) => f.project_id || null)).size === 1 ? moving[0].project_id || null : undefined}
          onPick={(target) => move(moving, target)}
          onClose={() => setMoving(null)}
        />
      ) : null}

      {undo && !chosen.length ? (
        <div className="select-bar toast" role="status">
          <span className="select-count">{undo.length === 1 ? `「${undo[0].filename}」` : `${undo.length} 个文件`}已移到回收站</span>
          <button className="select-action" onClick={() => restore(undo)}>
            撤销
          </button>
        </div>
      ) : null}

      {viewing ? (
        <FileViewer
          files={files}
          file={viewing}
          onOpen={setViewing}
          onClose={() => setViewing(null)}
          onChanged={(head) => {
            setViewing(head);
            reload();
          }}
          chatHref={(f) => chatHref([f])}
        />
      ) : null}

      {over ? (
        <div className="drop-overlay" onDragLeave={() => setOver(false)}>
          <IconUpload size={22} /> 松开即可上传{project ? `到「${project.name}」` : ""}
        </div>
      ) : null}
    </main>
  );
}

export default function FilesPage() {
  return (
    <Suspense fallback={null}>
      <Library />
    </Suspense>
  );
}
