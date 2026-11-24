import { afterEach, describe, expect, it, vi } from "vitest";
import { cachedMentions, fetchMentions, forgetMentions, highlightPieces } from "../lib/mentions";

afterEach(() => {
  forgetMentions();
  vi.restoreAllMocks();
});

describe("mentions", () => {
  it("asks once per scope and query and scopes by conversation before project", async () => {
    const item = { id: "fil_1", filename: "季度报告.pptx", highlight: [[0, 2]] };
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [item] }), { status: 200 }));
    const scope = { conversationId: "cnv_1", projectId: "prj_1" };
    expect(await fetchMentions(scope, "季度")).toEqual([item]);
    expect(await fetchMentions(scope, " 季度 ")).toEqual([item]);
    expect(fetch).toHaveBeenCalledTimes(1);
    const url = String(fetch.mock.calls[0][0]);
    expect(url).toContain("/v1/files/mentions?");
    expect(url).toContain("conversation_id=cnv_1");
    expect(url).not.toContain("project_id");
    expect(cachedMentions({ conversationId: "cnv_2" }, "季度")).toBeNull();
    forgetMentions();
    expect(cachedMentions(scope, "季度")).toBeNull();
  });

  it("splits the name by code points", () => {
    expect(highlightPieces("😀报告.docx", [[1, 3]])).toEqual([
      { text: "😀", hit: false },
      { text: "报告", hit: true },
      { text: ".docx", hit: false },
    ]);
    expect(highlightPieces("ab", [[5, 9]])).toEqual([{ text: "ab", hit: false }]);
  });
});
