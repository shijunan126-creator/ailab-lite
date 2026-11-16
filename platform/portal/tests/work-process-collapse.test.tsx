// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import TurnComponent, { type Item } from "../components/chat/turn";
import type { Run } from "../lib/api";

vi.mock("../components/chat/answer", () => ({ default: ({ text }: { text: string }) => <div>{text}</div> }));
vi.mock("../components/markdown", () => ({ default: ({ text }: { text: string }) => <div>{text}</div> }));
vi.mock("../components/motion/smooth-text", () => ({ useSmoothText: (text: string) => text }));
vi.mock("../components/preview/file-viewer", () => ({ useFileViewer: () => ({ open: vi.fn(), viewer: null }) }));
vi.mock("../components/viewport-block", () => ({ useViewport: () => ({ ref: null, visible: true, style: {} }) }));

const writing: Item = { kind: "thinking", step: 0, text: "**检查环境**\n先确认依赖。", seconds: 1, done: false };
const thought: Item = { ...writing, done: true };
const answer: Item = { kind: "text", step: 0, text: "正式回答", confirmed: false };
const step: Item = {
  kind: "steps",
  steps: [{ name: "bash", label: "运行命令", detail: "python -V", status: "running", modelStep: 0 }],
};
const nextThought: Item = { kind: "thinking", step: 1, text: "**写脚本**\n生成文档。", seconds: 1, done: false };

const run = (mode: "work" | "chat", status: "running" | "queued" | "waiting_user" | "interrupted" | "succeeded") =>
  ({
    id: "run-collapse",
    status,
    prompt: "任务",
    summary: status === "succeeded" ? "正式回答" : null,
    work_ms: 1000,
    options: { mode },
    result: {},
    input_files: [],
    artifacts: [],
  }) as unknown as Run;

function mount(items: Item[], status: "running" | "queued" | "waiting_user" | "interrupted" | "succeeded" = "running", mode: "work" | "chat" = "work") {
  return (
    <TurnComponent
      run={run(mode, status)}
      items={items}
      live={status === "running"}
      modelLabel="Model"
      previewKey={() => null}
      onPreview={() => {}}
      onPreviewFile={() => {}}
    />
  );
}

const head = () => document.querySelector<HTMLElement>(".steps-head")!;
const thoughtHeads = () => [...document.querySelectorAll<HTMLElement>(".pr-thought > button")];
const bodies = () => document.querySelectorAll(".pr-thought-body").length;

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test.each(["work", "chat"] as const)("a %s thought reads live and folds as soon as the answer starts", (mode) => {
  const view = render(mount([writing], "running", mode));
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText(/先确认依赖/)).toBeTruthy();

  view.rerender(mount([thought, answer], "running", mode));
  expect(head().getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("正式回答")).toBeTruthy();
});

test("a thought folds when a tool call follows it and the next thought opens; the process stays open", () => {
  const view = render(mount([writing]));
  view.rerender(mount([thought, step]));
  expect(head().getAttribute("aria-expanded")).toBe("true");
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("false");

  view.rerender(mount([thought, step, nextThought]));
  expect(thoughtHeads().map((button) => button.getAttribute("aria-expanded"))).toEqual(["false", "true"]);
  expect(head().getAttribute("aria-expanded")).toBe("true");
});

test("the reader's choice on a thought wins over its live state", () => {
  const view = render(mount([writing]));
  fireEvent.click(thoughtHeads()[0]);
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("false");
  view.rerender(mount([{ ...writing, text: writing.text + "再看版本。" }]));
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("false");

  fireEvent.click(thoughtHeads()[0]);
  fireEvent.click(head());
  fireEvent.click(head());
  view.rerender(mount([thought, answer]));
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("true");
  expect(bodies()).toBe(1);
});

test("a finished run is folded with every thought closed", () => {
  render(mount([thought, step, answer], "succeeded"));
  expect(head().getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(head());
  expect(thoughtHeads()[0].getAttribute("aria-expanded")).toBe("false");
  expect(bodies()).toBe(0);
});


test.each(["work", "chat"] as const)("%s process reopens for tools and respects manual expansion", (mode) => {
  const view = render(mount([thought, step], "running", mode));
  expect(head().getAttribute("aria-expanded")).toBe("true");
  view.rerender(mount([thought, answer], "running", mode));
  expect(head().getAttribute("aria-expanded")).toBe("false");
  view.rerender(mount([thought, answer, step], "running", mode));
  expect(head().getAttribute("aria-expanded")).toBe("true");
  view.rerender(mount([thought, answer], "running", mode));
  fireEvent.click(head());
  view.rerender(mount([thought, { ...answer, text: "更多回答" }], "running", mode));
  expect(head().getAttribute("aria-expanded")).toBe("true");
});


test.each(["waiting_user", "interrupted"] as const)("%s keeps user actions visible with trailing text", (status) => {
  render(mount([thought, { kind: "ask", id: "question", question: "继续？", options: ["继续"] } as Item, answer], status));
  expect(head().getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("继续？")).toBeTruthy();
});
