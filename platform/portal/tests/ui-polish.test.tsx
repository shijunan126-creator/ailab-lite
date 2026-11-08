// @vitest-environment jsdom
// Wrapped diagram labels, the composer's empty state, the skill form's SKILL.md and pictures in answers.
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import Answer from "../components/chat/answer";
import { readParts } from "../components/chat/rich-input";
import { answerForCopy } from "../components/genui/markdown-bridge";
import { shapeOk, siteOf, webImage } from "../components/genui/catalog/data";
import { textWidth, wrap } from "../components/genui/catalog/kit";
import { buildSkillMarkdown, readSkillMarkdown, skillSlug } from "../components/skills/skill-save";

vi.mock("../components/genui/telemetry", () => ({ report: () => undefined }));
afterEach(cleanup);

const block = (...lines: string[]) => ["```openui", ...lines, "```"].join("\n");
const SIGNED = "/v1/web-images/aHR0cHM6Ly9leGFtcGxlLmNvbS9hLmpwZw.c2ln";

test("a label wraps into lines instead of being cut, and keeps Latin words whole", () => {
  const lines = wrap("Prefill 阶段计算全部 prompt token 的 KV 缓存", 96, 4);
  assert.ok(lines.length > 1);
  assert.equal(lines.join("").replace(/\s/g, ""), "Prefill阶段计算全部prompttoken的KV缓存");
  assert.ok(lines.every((l) => textWidth(l) <= 96));
  assert.ok(!lines.some((l) => l.endsWith("…")));
  for (const word of ["Prefill", "prompt", "token"]) assert.ok(lines.some((l) => l.includes(word)), word);
  // only text beyond the last allowed line is shortened
  const capped = wrap("一二三四五六七八九十".repeat(6), 60, 2);
  assert.equal(capped.length, 2);
  assert.ok(capped[1].endsWith("…"));
  assert.deepEqual(wrap("短", 60), ["短"]);
});

test("Shift+Enter then Backspace leaves an empty message, not a newline", () => {
  const root = document.createElement("div");
  root.append("\n");
  assert.deepEqual(readParts(root), []);
  root.replaceChildren("第一行\n第二行\n");
  assert.deepEqual(readParts(root), [{ kind: "text", text: "第一行\n第二行" }]);
  root.replaceChildren("a", document.createElement("br"));
  assert.deepEqual(readParts(root), [{ kind: "text", text: "a" }]);
});

test("the skill form writes a SKILL.md and reads a pasted one back into its fields", () => {
  assert.equal(skillSlug("Weekly Report!", () => "ab12"), "weekly-report-ab12");
  assert.equal(skillSlug("周报助手", () => "zz"), "skill-zz00");
  const md = buildSkillMarkdown({ name: "weekly-ab12", title: "周报\n助手", description: "写周报时", body: "\n步骤一\n" });
  assert.equal(md, "---\nname: weekly-ab12\ntitle: 周报 助手\ndescription: 写周报时\n---\n\n步骤一\n");
  assert.deepEqual(readSkillMarkdown(md), { name: "weekly-ab12", title: "周报 助手", description: "写周报时", body: "步骤一\n" });
  assert.equal(readSkillMarkdown("# 只是正文"), null);
  assert.equal(readSkillMarkdown("---\nname: Bad Name\ndescription: x\n---\nbody")!.name, "");
});

test("only platform-signed web images load; the source shows as its site", () => {
  assert.equal(webImage(SIGNED), SIGNED);
  assert.equal(webImage("img_AbCdEf123456"), "img_AbCdEf123456");
  for (const bad of ["https://evil.example/x.png?leak=1", "/v1/files/f1/content", "/v1/web-images/a", `${SIGNED}/../x`, "data:image/png;base64,AA"]) {
    assert.equal(webImage(bad), null, bad);
  }
  assert.equal(siteOf("https://www.cartube.co.il/news/1"), "cartube.co.il");
  assert.equal(siteOf("javascript:alert(1)"), null);
  assert.equal(shapeOk("Gallery", { images: [] }), false);
  assert.equal(shapeOk("Gallery", { images: [{ src: SIGNED }] }), true);
  assert.equal(shapeOk("ImageText", { src: SIGNED }), false);
});

test("a gallery shows three tiles, counts the rest, and opens full size", async () => {
  const images = Array.from({ length: 6 }, (_, i) => `{src: "${SIGNED.replace("c2ln", `s${i}`)}", alt: "图${i + 1}", source: "https://www.cartube.co.il/p"}`);
  const text = block(`root = Gallery([${images.join(", ")}], {title: "赛车"})`);
  const { container } = render(<Answer text={text} />);
  await waitFor(() => assert.ok(container.querySelector(".gu-gallery-row img")));
  const tiles = container.querySelectorAll(".gu-gallery-row img");
  assert.equal(tiles.length, 3);
  assert.equal(tiles[0].getAttribute("src"), `/api${SIGNED.replace("c2ln", "s0")}?variant=thumb`);
  assert.equal(container.querySelector(".gu-gallery-more.wide")?.textContent, "+3");
  fireEvent.click(screen.getByRole("button", { name: "查看大图：图1" }));
  assert.equal(screen.getByRole("dialog", { name: "图1" }).querySelector(".gu-lightbox-count")?.textContent, "1 / 6");
  fireEvent.keyDown(document, { key: "ArrowRight" });
  assert.ok(screen.getByRole("dialog", { name: "图2" }));
  fireEvent.keyDown(document, { key: "Escape" });
  assert.equal(screen.queryByRole("dialog"), null);
});

test("a picture the model wrote itself never loads, and copy names the picture and its source", async () => {
  const text = block(
    'root = Stack([ImageText("https://evil.example/x.png", "维斯塔潘", {text: "车手", source: "https://example.com/p"}), Image("' + SIGNED + '", {caption: "赛道", source: "https://www.cartube.co.il/a"})])',
  );
  const { container } = render(<Answer text={text} />);
  await waitFor(() => assert.ok(container.querySelector(".gu-imagetext")));
  const loaded = [...container.querySelectorAll("img")].map((i) => i.getAttribute("src"));
  assert.deepEqual(loaded, [`/api${SIGNED}?variant=thumb`]);
  assert.ok(container.querySelector(".gu-imagetext .gu-photo-missing"));
  const copied = answerForCopy(text);
  assert.match(copied, /维斯塔潘/);
  assert.match(copied, /赛道/);
  assert.match(copied, /cartube\.co\.il/);
  assert.doesNotMatch(copied, /evil\.example/);
});

test("short image IDs use the share scope and fetch original only on opening", async () => {
  const { GenUIHostProvider } = await import("../components/genui/host");
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ source: "https://news.example.com/story" }) }));
  const observers: (() => void)[] = [];
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) { observers.push(() => callback([{ isIntersecting: true }])); }
    observe() {} disconnect() {}
  });
  try {
    const { container } = render(<GenUIHostProvider value={{ readOnly: true, shareId: "shr_test" }}>
      <Answer text={block('root = Image("img_AbCdEf123456", {caption: "赛道"})')} />
    </GenUIHostProvider>);
    await waitFor(() => assert.ok(container.querySelector("img")));
    assert.equal(container.querySelector("img")!.getAttribute("src"), "/api/v1/web-images/img_AbCdEf123456?variant=thumb&share_id=shr_test");
    assert.equal(fetcher.mock.calls.length, 0);
    observers.forEach((notify) => notify());
    await waitFor(() => assert.ok(screen.getByText("news.example.com")));
    fireEvent.click(screen.getByRole("button", { name: "查看大图：赛道" }));
    const full = screen.getByRole("dialog").querySelector("img")!;
    assert.equal(full.getAttribute("src"), "/api/v1/web-images/img_AbCdEf123456?variant=original&share_id=shr_test");
    fireEvent.error(full);
    assert.ok(full.getAttribute("src")!.includes("variant=fallback"));
    fireEvent.error(full);
    assert.ok(screen.getByRole("dialog").querySelector(".gu-photo-missing"));
  } finally {
    cleanup();
    vi.unstubAllGlobals();
  }
});
