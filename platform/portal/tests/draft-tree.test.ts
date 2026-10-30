import { describe, expect, it } from "vitest";
import { buildTree, pathProblem } from "../lib/draft-tree";
import type { DraftFile } from "../lib/api";

const f = (path: string, change: DraftFile["change"] = "unchanged", kind: DraftFile["kind"] = "text"): DraftFile => ({ path, size: 1, kind, change });

describe("skill draft tree", () => {
  it("nests files under folders, SKILL.md first, then folders, then files", () => {
    const tree = buildTree([f("scripts/run.py"), f("README.md"), f("SKILL.md"), f("assets/img/a.png", "unchanged", "image")]);
    expect(tree.map((n) => n.path)).toEqual(["SKILL.md", "assets", "scripts", "README.md"]);
    expect(tree[1].children![0].path).toBe("assets/img");
    expect(tree[1].children![0].children![0].file?.kind).toBe("image");
  });

  it("marks a folder by what changed inside it", () => {
    const tree = buildTree([f("SKILL.md", "modified"), f("new/a.py", "added"), f("new/b.py", "added"), f("old/x.py", "deleted"), f("mix/a", "added"), f("mix/b")]);
    const by = Object.fromEntries(tree.map((n) => [n.path, n.change]));
    expect(by).toMatchObject({ new: "added", old: "deleted", mix: "modified", "SKILL.md": "modified" });
  });

  it("keeps empty folders made in the editor", () => {
    expect(buildTree([f("SKILL.md")], ["refs/notes"]).map((n) => n.path)).toEqual(["SKILL.md", "refs"]);
  });

  it("rejects paths the API would refuse", () => {
    expect(pathProblem("a/../b")).not.toBe("");
    expect(pathProblem("/abs")).not.toBe("");
    expect(pathProblem("a\\b")).not.toBe("");
    expect(pathProblem("a//b")).not.toBe("");
    expect(pathProblem("scripts/run.py")).toBe("");
  });
});
