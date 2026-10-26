"use client";

import { useEffect, useState } from "react";
import { api, Project } from "./api";
import { confirmDialog } from "./dialog";

/** Pages tell each other (and the sidebar) that projects changed: created, renamed, deleted, files moved. */
export const PROJECTS_CHANGED = "ailab:projects";
export function projectsChanged(): void {
  window.dispatchEvent(new Event(PROJECTS_CHANGED));
}

// One list for the whole page: the sidebar, the chat crumb and the library all read it, one request serves them.
let cache: Project[] | null = null;
let inflight: Promise<void> | null = null;
let stale = false;
const listeners = new Set<(list: Project[]) => void>();

/** Fetch the list. `changed`: something was just modified, so an answer already on its way may predate it. */
function refresh(changed = false): Promise<void> {
  if (inflight) {
    if (changed) stale = true; // fetch once more when the current request returns
    return inflight;
  }
  inflight = api<Project[]>("/v1/projects")
    .then((list) => {
      cache = list;
      listeners.forEach((fn) => fn(list));
    })
    .catch(() => undefined)
    .finally(() => {
      inflight = null;
      if (stale) {
        stale = false;
        refresh();
      }
    });
  return inflight;
}

if (typeof window !== "undefined") window.addEventListener(PROJECTS_CHANGED, () => refresh(true));

export function useProjects(): Project[] | null {
  const [projects, setProjects] = useState<Project[] | null>(cache);
  useEffect(() => {
    listeners.add(setProjects);
    if (cache) setProjects(cache);
    else refresh(); // readers mounting together share the one request
    return () => {
      listeners.delete(setProjects);
    };
  }, []);
  return projects;
}

export async function createProject(name: string): Promise<Project> {
  const project = await api<Project>("/v1/projects", { method: "POST", body: JSON.stringify({ name }) });
  cache = [project, ...(cache || [])]; // a page opened for it right away must already find it
  listeners.forEach((fn) => fn(cache!));
  projectsChanged();
  return project;
}

export async function renameProject(id: string, name: string): Promise<void> {
  await api(`/v1/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
  projectsChanged();
}

/** Ask first, then delete: false when the user declines. Callers also refresh the conversation list. */
export async function deleteProject(project: Project): Promise<boolean> {
  const lost = project.conversations ? `其中的 ${project.conversations} 个对话会一起删除。` : "";
  const kept = project.files ? `${project.files} 个文件会移到回收站。` : "";
  if (!(await confirmDialog({ title: `删除项目「${project.name}」？`, body: `${lost}${kept}` || undefined, action: "删除", danger: true }))) return false;
  await api(`/v1/projects/${project.id}`, { method: "DELETE" });
  projectsChanged();
  return true;
}

// The sidebar's "项目说明" opens the brief editor on the project page, which may mount after the click.
export const EDIT_BRIEF = "ailab:edit-brief";
let briefRequest: string | null = null;
export function requestBriefEdit(projectId: string): void {
  briefRequest = projectId;
  window.dispatchEvent(new CustomEvent(EDIT_BRIEF, { detail: projectId }));
}
export function takeBriefEdit(projectId: string): boolean {
  if (briefRequest !== projectId) return false;
  briefRequest = null;
  return true;
}
