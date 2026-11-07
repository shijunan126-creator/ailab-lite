// @vitest-environment jsdom
import React from "react";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, test, vi} from "vitest";
import TextExtraction, {parsePages} from "../components/library/text-extraction";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {"Content-Type": "application/json"}});
beforeEach(() => vi.stubGlobal("React", React));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("opening reads at once: a short PDF is read whole and its text is shown to copy", async () => {
  const fetched: string[] = [];
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    fetched.push(`${init?.method || "GET"} ${path}`);
    if (init?.method === "POST") { bodies.push(JSON.parse(String(init.body))); return json({request_id: "r1"}); }
    if (path === "/api/v1/files/f1") return json({id: "f1", meta: {extract: {units: 3}}});
    if (path.includes("extracted-text")) return new Response("第一页\n第二页");
    return json({status: "done", download_url: "/v1/files/f1/extracted-text?request_id=r1"});
  }));
  render(<TextExtraction fileId="f1" filename="报告.pdf" onClose={() => {}} />);
  await screen.findByLabelText("报告.pdf 的文字");
  expect(screen.getByLabelText("报告.pdf 的文字").textContent).toBe("第一页\n第二页");
  expect(screen.queryByRole("textbox", {name: "页码"})).toBeNull(); // three pages need no range
  expect(bodies).toEqual([{pages: [1, 2, 3]}]);
  expect(screen.getByRole("link", {name: "下载"}).getAttribute("href")).toBe("/api/v1/files/f1/extracted-text?request_id=r1");
  expect(screen.getByRole("button", {name: "复制全部"})).toBeTruthy();
  expect(fetched.filter((f) => f.startsWith("POST"))).toEqual(["POST /api/v1/files/f1/extract-text"]);
});

test("a long PDF reads its first batch and offers another range", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") { bodies.push(JSON.parse(String(init.body))); return json({request_id: `r${bodies.length}`}); }
    if (path === "/api/v1/files/f1") return json({id: "f1", meta: {extract: {pages: 45}}});
    if (path.includes("extracted-text")) return new Response("文字");
    return json({status: "done", download_url: "/v1/files/f1/extracted-text?request_id=r"});
  }));
  render(<TextExtraction fileId="f1" filename="长报告.pdf" onClose={() => {}} />);
  await screen.findByLabelText("长报告.pdf 的文字");
  expect((screen.getByRole("textbox", {name: "页码"}) as HTMLInputElement).value).toBe("1-20");
  fireEvent.change(screen.getByRole("textbox", {name: "页码"}), {target: {value: "21-22"}});
  fireEvent.click(screen.getByRole("button", {name: "识别"}));
  await waitFor(() => expect(bodies).toEqual([{pages: Array.from({length: 20}, (_, i) => i + 1)}, {pages: [21, 22]}]));
});

test("a failed read can be retried and never offers a download", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST"
    ? json({request_id: "old-request"}) : json({detail: "提取请求已变更，请重新提取"}, 409)));
  render(<TextExtraction fileId="f1" filename="图片.png" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("提取请求已变更"));
  expect(screen.queryByRole("link", {name: "下载"})).toBeNull();
  expect(screen.getByRole("button", {name: "重试"})).toBeTruthy();
});

test("page ranges", () => {
  expect(parsePages("1-3, 5", 10)).toEqual([1, 2, 3, 5]);
  expect(parsePages("2，2-3", null)).toEqual([2, 3]);
  expect(() => parsePages("8-12", 10)).toThrow("1–10");
  expect(() => parsePages("1-21", null)).toThrow("最多 20 页");
  expect(() => parsePages("a", 10)).toThrow("页码格式");
});
