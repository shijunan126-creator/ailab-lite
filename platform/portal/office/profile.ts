import type { DocKind } from "#office/kinds.ts";

export interface Profile {
  name: string;
  version: string;
  title: { en: string; "zh-CN": string };
  kinds: readonly DocKind[];
}

// One viewer opens all four formats in the Portal side pane.
export const PROFILE: Profile = {
  name: "office",
  version: "1",
  title: { en: "Document", "zh-CN": "文档" },
  kinds: ["docx", "xlsx", "pptx", "pdf"],
};
