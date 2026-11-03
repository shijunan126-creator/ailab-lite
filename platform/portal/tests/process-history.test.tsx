// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import TurnComponent from "../components/chat/turn";
import type { Run } from "../lib/api";

vi.mock("../components/chat/answer", () => ({ default: ({ text }: { text: string }) => <div>{text}</div> }));
vi.mock("../components/markdown", () => ({ default: ({ text }: { text: string }) => <div>{text}</div> }));
vi.mock("../components/preview/file-viewer", () => ({ useFileViewer: () => ({ open: vi.fn(), viewer: null }) }));
vi.mock("../components/viewport-block", () => ({ useViewport: () => ({ ref: null, visible: true, style: {} }) }));
function Turn({ run, live }: { run: Run; live: boolean }) {
 return <TurnComponent run={run} live={live} items={undefined} modelLabel="Model" previewKey={() => null} onPreview={() => {}} onPreviewFile={() => {}} />;
}
const run = (id: string) => ({ id, status: "succeeded", prompt: "任务", summary: "回答", work_ms: 1000, options: { mode: "work" }, result: {}, input_files: [], artifacts: [] } as unknown as Run);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(fetcher: any) {
 vi.stubGlobal("fetch", fetcher);
 vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
 vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
}
const response = (items: any[]) => new Response(JSON.stringify({ schema_version: 1, source: "snapshot", items }), { headers: { "Content-Type": "application/json" } });

test("historical process reads ProcessView including narration without events/history", async () => {
 const fetcher = vi.fn(async (_url: unknown) => response([{ kind: "text", step: 0, text: "历史旁白" }, { kind: "thinking", step: 1, text: "完整思考", seconds: 2, done: true }]));
 setup(fetcher);
 render(<Turn run={run("history")} live={false} />);
 fireEvent.click(screen.getByRole("button", { name: /工作总时长/ }));
 await waitFor(() => expect(screen.getByText("历史旁白")).toBeTruthy());
 expect(fetcher.mock.calls[0][0]).toContain("/v1/runs/history/process");
 fireEvent.click(screen.getByRole("button", { name: /完整思考/ }));
 expect(screen.getAllByText("完整思考").length).toBeGreaterThan(0);
});

test("an empty snapshot is an empty process, not expired-event failure", async () => {
 setup(vi.fn(async () => response([])));
 render(<Turn run={run("empty")} live={false} />);
 fireEvent.click(screen.getByRole("button", { name: /工作总时长/ }));
 await waitFor(() => expect(screen.getByText("没有可显示的过程")).toBeTruthy());
 expect(screen.queryByText("加载失败，重试")).toBeNull();
});

test("changing run aborts and ignores a stale process fetch", async () => {
 let resolve!: (value: Response) => void;
 let signal!: AbortSignal;
 setup(vi.fn((_url, init) => { signal = init.signal; return new Promise<Response>((r) => { resolve = r; }); }));
 const view = render(<Turn run={run("first")} live={false} />);
 fireEvent.click(screen.getByRole("button", { name: /工作总时长/ }));
 await waitFor(() => expect(signal).toBeTruthy());
 const oldSignal = signal;
 const oldResolve = resolve;
 view.rerender(<Turn run={run("second")} live={false} />);
 expect(oldSignal.aborted).toBe(true);
 await act(async () => oldResolve(response([{ kind: "text", step: 0, text: "stale" }])));
 expect(screen.queryByText("stale")).toBeNull();
 view.unmount();
 expect(signal.aborted).toBe(true);
});


test("a historical durable pause loads ProcessView and keeps its saved answer", async () => {
 const fetcher = vi.fn(async (_url: unknown) => response([]));
 setup(fetcher);
 render(<Turn run={{ ...run("pause"), status: "interrupted", summary: "已完成的部分" }} live={false} />);
 await waitFor(() => expect(screen.getByText("没有可显示的过程")).toBeTruthy());
 expect(screen.getByText("已完成的部分")).toBeTruthy();
 expect(fetcher.mock.calls[0][0]).toContain("/v1/runs/pause/process");
});
