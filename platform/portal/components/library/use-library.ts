"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {FileItem, api, errorMessage, filesByIds} from "../../lib/api";

export type Category = "all" | "starred" | "images" | "slides" | "trash";
export type LibraryFilter = { category: Category; projectId?: string | null; q?: string; grouped?: boolean };

export type LibraryFolder = {id: string; name: string; files: number};

const PAGE = 100;
const MAX = 200; // the API's largest page

function query(filter: LibraryFilter, limit: number, offset: number, cursor?: string): string {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (cursor) params.set("cursor", cursor);
  if (filter.category === "trash") params.set("trashed", "true");
  else params.set("category", filter.category);
  if (filter.projectId) params.set("project_id", filter.projectId);
  else if (filter.grouped) params.set("scope", "root");
  if (filter.q?.trim()) params.set("q", filter.q.trim());
  return `/v1/files?${params}`;
}

/** One filtered view of the library. Responses that arrive after the filter changed are dropped. */
export function useLibrary(filter: LibraryFilter, enabled = true) {
  const [folders, setFolders] = useState<LibraryFolder[]>([]);
  const [folderMore, setFolderMore] = useState(false);
  const folderCursor = useRef("");
  const [files, setFiles] = useState<FileItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
  const seq = useRef(0); // every request: drops a response overtaken by a newer one
  const view = useRef(0); // the filter: drops scan polls started for a view that is no longer shown
  const count = useRef(0);
  const fileCursor = useRef<string | undefined>(undefined);
  const projectFiles = !!filter.projectId && filter.category === "all";
  const key = `${filter.category}|${filter.projectId || ""}|${filter.q?.trim() || ""}|${filter.grouped || false}`;
  const folderQuery = (after = "") => {
    const params = new URLSearchParams({limit: String(PAGE), after});
    if (filter.category === "trash") params.set("trashed", "true");
    else params.set("category", filter.category);
    if (filter.q?.trim()) params.set("q", filter.q.trim());
    return `/v1/files/folders?${params}`;
  };
  const fetchFolders = (after = "") => filter.grouped && !filter.projectId ? api<LibraryFolder[]>(folderQuery(after)) : Promise.resolve([]);

  // Refetch what is on screen (at least one page): after a change, and while scans are pending.
  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const limit = Math.min(MAX, Math.max(PAGE, count.current));
    const fetchLimit = Math.min(MAX, limit + Number(projectFiles));
    setLoading(true);
    try {
      const [page, grouped] = await Promise.all([api<FileItem[]>(query(filter, fetchLimit, 0)), fetchFolders()]);
      if (mine !== seq.current) return;
      const list = page.slice(0, limit);
      setFolders(grouped);
      folderCursor.current = grouped.at(-1)?.id || "";
      setFolderMore(grouped.length === PAGE);
      count.current = list.length;
      fileCursor.current = list.at(-1)?.id;
      setFiles(list);
      setMore(page.length === fetchLimit);
      setError("");
    } catch (err: any) {
      if (mine === seq.current) setError(errorMessage(err));
    } finally {
      if (mine === seq.current) {
        setLoaded(true);
        setLoading(false);
      }
    }
  }, [key]);

  const loadMore = useCallback(async () => {
    if (loading) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const [page, grouped] = await Promise.all([
        api<FileItem[]>(query(filter, PAGE + Number(projectFiles), projectFiles ? 0 : count.current, projectFiles ? fileCursor.current : undefined)),
        folderMore ? fetchFolders(folderCursor.current) : Promise.resolve([]),
      ]);
      if (mine !== seq.current) return;
      const list = page.slice(0, PAGE);
      fileCursor.current = list.at(-1)?.id;
      if (folderMore) {
        setFolders(prev => [...prev, ...grouped]);
        folderCursor.current = grouped.at(-1)?.id || folderCursor.current;
        setFolderMore(grouped.length === PAGE);
      }
      setFiles((prev) => {
        const seen = new Set(prev.map((f) => f.id));
        const next = [...prev, ...list.filter((f) => !seen.has(f.id))];
        count.current = next.length;
        return next;
      });
      setMore(page.length === PAGE + Number(projectFiles));
      setError("");
    } catch (err: any) {
      if (mine === seq.current) setError(errorMessage(err));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [key, folderMore, loading]);

  useEffect(() => {
    if (!enabled) return;
    count.current = 0;
    fileCursor.current = undefined;
    setLoaded(false);
    setMore(false);
    setError("");
    setFiles([]);
    reload();
    return () => {
      seq.current++;
      view.current++;
    };
  }, [reload, enabled]);

  // Uploads stay "pending" until the Worker has scanned them. Ask about those alone, in one request.
  const pending = files
    .filter((f) => f.status === "pending")
    .map((f) => f.id)
    .join(",");
  useEffect(() => {
    if (!enabled || !pending) return;
    const mine = view.current;
    const controller = new AbortController();
    const ids = pending.split(",");
    const requested = new Set(ids);
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]);
      const fresh: FileItem[] | null = await filesByIds(ids, signal).catch(() => null);
      // only a change of filter makes this stale: a reload or "load more" meanwhile (star, move...) must not
      // throw the scan result away, or the file would show 扫描中 until the page is refreshed
      if (controller.signal.aborted || mine !== view.current) return;
      if (fresh) {
        const byId = new Map(fresh.map((f) => [f.id, f] as const));
        setFiles((prev) => prev.filter((f) => f.status !== "pending" || !requested.has(f.id) || byId.has(f.id)).map((f) => {
          const scanned = byId.get(f.id);
          // A concurrent reload/mutation may already have newer status, star or project data.
          return f.status === "pending" && scanned ? { ...f, status: scanned.status, meta: scanned.meta,
            preview: scanned.preview, thumbnail_url: scanned.thumbnail_url } : f;
        }));
      }
      timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [pending, enabled]);

  return { files, folders, setFiles, loaded, loading, more: more || folderMore, error, reload, loadMore };
}
