import { describe, expect, it } from "vitest";
import { fromPayload, matchSkills, normalize, sentSkills, slashQuery, toPayload, type Part } from "../lib/message-parts";

const skill = (name: string, title = name): Part => ({ kind: "skill", name, title });
const text = (t: string): Part => ({ kind: "text", text: t });

describe("message parts", () => {
  it("places skills by code point and trims like the old composer", () => {
    const parts = [text("  用"), skill("pptx", "PPT 设计"), text("做😀一份，再按"), skill("weekly"), text("写摘要 ")];
    expect(toPayload(parts)).toEqual({
      prompt: "用做😀一份，再按写摘要",
      skills: [
        { name: "pptx", file_id: null, at: 1, title: "PPT 设计" },
        { name: "weekly", file_id: null, at: 8, title: "weekly" },
      ],
    });
  });

  it("keeps the first place of a repeated skill and clamps skills at the edges", () => {
    expect(toPayload([skill("a"), text(" hi "), skill("a"), skill("b")]).skills).toEqual([
      { name: "a", file_id: null, at: 0, title: "a" },
      { name: "b", file_id: null, at: 2, title: "b" },
    ]);
  });

  it("round-trips through the payload", () => {
    const parts = normalize([text("先"), skill("pptx", "PPT 设计"), text("后😀"), skill("x", "X")]);
    const { prompt, skills } = toPayload(parts);
    const back = fromPayload(prompt, skills);
    expect(back).toEqual(parts.map((p) => (p.kind === "skill" ? { ...p, fileId: null } : p)));
  });

  it("puts skills without a place at the end and reads older runs", () => {
    expect(fromPayload("hi", sentSkills({ skill: "pptx", skill_title: "PPT" }))).toEqual([text("hi"), { kind: "skill", name: "pptx", title: "PPT", fileId: null }]);
    expect(sentSkills({})).toEqual([]);
  });

  it("opens the list only where a skill can start", () => {
    expect(slashQuery("/")).toEqual({ query: "", length: 1 });
    expect(slashQuery("请用 /pp")).toEqual({ query: "pp", length: 3 });
    expect(slashQuery("第一行\n/周报")).toEqual({ query: "周报", length: 3 });
    expect(slashQuery("A/B")).toBeNull();
    expect(slashQuery("看 https://a.cn/x")).toBeNull();
    expect(slashQuery("~/dir")).toBeNull();
    expect(slashQuery("/a b")).toBeNull();
  });

  it("matches by title or name, title prefix first", () => {
    const list = [{ name: "weekly", title: "周报助手" }, { name: "pptx", title: "PPT 设计" }, { name: "platform.pptx", title: "PPT" }];
    expect(matchSkills(list, "ppt").map((s) => s.name)).toEqual(["pptx", "platform.pptx"]);
    expect(matchSkills(list, "周").map((s) => s.name)).toEqual(["weekly"]);
    expect(matchSkills(list, "zzz")).toEqual([]);
  });
});
