// @vitest-environment jsdom
import React from "react";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, test, vi} from "vitest";
import TextExtraction from "../components/library/text-extraction";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {"Content-Type": "application/json"}});
beforeEach(() => vi.stubGlobal("React", React));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("polling and download stay bound to the selected extraction request", async () => {
  const fetched: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    fetched.push(`${init?.method || "GET"} ${path}`);
    return init?.method === "POST" ? json({request_id: "request-2", ocr: "done"})
      : json({status: "done", download_url: "/v1/files/f1/extracted-text?request_id=request-2"});
  }));
  render(<TextExtraction fileId="f1" filename="报告.pdf" onClose={() => {}} />);
  fireEvent.change(screen.getByRole("textbox", {name: "提取页码"}), {target: {value: "1-2"}});
  fireEvent.click(screen.getByRole("button", {name: "提取"}));
  const link = await screen.findByRole("link", {name: "下载文字"});
  expect(link.getAttribute("href")).toBe("/api/v1/files/f1/extracted-text?request_id=request-2");
  expect(fetched).toEqual(["POST /api/v1/files/f1/extract-text", "GET /api/v1/files/f1/extract-text?request_id=request-2"]);
});

test("a superseded request shows an error and cannot download a different range", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => init?.method === "POST"
    ? json({request_id: "old-request"}) : json({detail: "提取请求已变更，请重新提取"}, 409)));
  render(<TextExtraction fileId="f1" filename="图片.png" onClose={() => {}} />);
  fireEvent.click(screen.getByRole("button", {name: "提取"}));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("提取请求已变更"));
  expect(screen.queryByRole("link", {name: "下载文字"})).toBeNull();
});
