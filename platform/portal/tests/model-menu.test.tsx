// @vitest-environment jsdom
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, test, vi } from "vitest";
import ModelMenu from "../components/chat/model-menu";
import type { ModelInfo } from "../lib/api";

afterEach(cleanup);

const think = { default: "low", levels: [{ id: "low", name: "低" }, { id: "high", name: "高" }, { id: "max", name: "最高" }] };
const models: ModelInfo[] = [
  { id: "flash", name: "DeepSeek Flash", description: "", weight: 1, tools: true, default: true, reasoning: think },
  { id: "demo", name: "离线演示", description: "", weight: 0.1, tools: true, default: false },
];

test("the button names the model and its level; a level picks that model and closes the menu", () => {
  const onChange = vi.fn();
  render(<ModelMenu models={models} value="flash" levelOf={(id) => (id === "flash" ? "high" : undefined)} onChange={onChange} />);
  const button = screen.getByRole("button", { name: "模型" });
  assert.equal(button.textContent, "DeepSeek Flash高");
  fireEvent.click(button);
  const levels = screen.getByRole("listbox", { name: "思考强度" });
  const options = within(levels).getAllByRole("option");
  assert.deepEqual(options.map((o) => [o.textContent, o.getAttribute("aria-selected")]), [["低", "false"], ["高", "true"], ["最高", "false"]]);
  fireEvent.click(options[2]);
  assert.deepEqual(onChange.mock.calls, [["flash", "max"]]);
  assert.equal(screen.queryByRole("listbox", { name: "思考强度" }), null);
});

test("the levels follow the model under the pointer; a model without levels offers none", () => {
  const onChange = vi.fn();
  render(<ModelMenu models={models} value="flash" levelOf={() => "low"} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "模型" }));
  const list = screen.getByRole("listbox", { name: "模型" });
  fireEvent.mouseEnter(within(list).getByRole("option", { name: /离线演示/ }));
  assert.equal(within(screen.getByRole("listbox", { name: "思考强度" })).queryAllByRole("option").length, 0);
  fireEvent.click(within(list).getByRole("option", { name: /离线演示/ }));
  assert.deepEqual(onChange.mock.calls, [["demo", undefined]]);
});

test("without any model that has levels the menu is a plain list", () => {
  render(<ModelMenu models={[models[1]]} value="demo" levelOf={() => undefined} onChange={() => undefined} />);
  const button = screen.getByRole("button", { name: "模型" });
  assert.equal(button.textContent, "离线演示");
  fireEvent.click(button);
  assert.equal(screen.queryByRole("listbox", { name: "思考强度" }), null);
});
