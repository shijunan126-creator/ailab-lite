// The directory tree of a skill draft, built from its flat file list (contract §3.1). Folders exist only through
// their files, except the empty ones the user just made in the editor, which live in the page until a file lands there.

import type { DraftFile } from "./api";

export type TreeNode = {
  name: string;
  path: string;
  dir: boolean;
  file?: DraftFile;
  change?: DraftFile["change"];
  children?: TreeNode[];
};

export function buildTree(files: DraftFile[], folders: Iterable<string> = []): TreeNode[] {
  const root: TreeNode = { name: "", path: "", dir: true, children: [] };
  const dirs = new Map<string, TreeNode>([["", root]]);
  const dir = (path: string): TreeNode => {
    const known = dirs.get(path);
    if (known) return known;
    const cut = path.lastIndexOf("/");
    const node: TreeNode = { name: path.slice(cut + 1), path, dir: true, children: [] };
    dir(cut < 0 ? "" : path.slice(0, cut)).children!.push(node);
    dirs.set(path, node);
    return node;
  };
  for (const folder of folders) if (folder) dir(folder);
  for (const file of files) {
    const cut = file.path.lastIndexOf("/");
    dir(cut < 0 ? "" : file.path.slice(0, cut)).children!.push({ name: file.path.slice(cut + 1), path: file.path, dir: false, file, change: file.change });
  }
  const settle = (node: TreeNode) => {
    for (const child of node.children!) if (child.dir) settle(child);
    node.children!.sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name, "zh-Hans-CN"));
    const changes = new Set(node.children!.map((c) => c.change).filter((c) => c && c !== "unchanged"));
    const live = node.children!.filter((c) => c.change !== "deleted");
    if (node.children!.length && !live.length) node.change = "deleted";
    else if (changes.size) node.change = changes.size === 1 && changes.has("added") && live.every((c) => c.change === "added") ? "added" : "modified";
  };
  settle(root);
  return root.children!;
}

const order = (n: TreeNode) => (n.path === "SKILL.md" ? 0 : n.dir ? 1 : 2);

/** Why a path cannot be used for a draft file, or "" (the API enforces the same rules). */
export function pathProblem(path: string): string {
  if (!path) return "请输入名称";
  if (path.startsWith("/") || path.includes("\\")) return "路径不能以 / 开头或包含 \\";
  if (path.split("/").some((part) => !part || part === "." || part === "..")) return "路径里有空的、. 或 .. 部分";
  if (path.length > 512) return "路径太长";
  return "";
}

export const joinPath = (folder: string, name: string) => (folder ? `${folder}/${name}` : name).replace(/\/+/g, "/");
export const parentOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
export const within = (path: string, folder: string) => path === folder || path.startsWith(`${folder}/`);
