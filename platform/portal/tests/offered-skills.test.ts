import assert from "node:assert/strict";
import { test } from "vitest";
import { offeredSkills } from "../components/chat/composer";
import type { Skill } from "../lib/api";

const skill = (name: string, extra: Partial<Skill>): Skill => ({ name, title: name, description: "", builtin: true, enabled: true, scope: "builtin", ...extra });

test("the always-on office skills are not offered for picking", () => {
  const offered = offeredSkills([
    skill("platform.pptx", { kind: "base", toggleable: false, extras: ["ppt_template"] }),
    skill("platform.docx", { kind: "base", toggleable: false }),
    skill("pptx", { kind: "builtin", toggleable: true, extras: ["ppt_template"] }),
    skill("auto-skill", { kind: "builtin", implicit: true }),
    skill("weekly", { kind: "personal", builtin: false, scope: "personal", file_id: "fil_1" }),
  ]);
  assert.deepEqual(offered.map((s) => s.name), ["pptx", "weekly"]);
  assert.equal(offered[0].template, true); // the PPT template stays reachable through the optional PPT skill
});
