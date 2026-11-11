// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ScheduledMark from "../components/chat/scheduled-mark";
import type { Schedule } from "../lib/api";
import { occurrenceText, stateText, when } from "../lib/schedules";

const task = (patch: Partial<Schedule>): Schedule => ({
  id: "sch_1", title: "早报", prompt: "汇总新闻", mode: "chat", skill: null, model: null, project_id: null,
  schedule: { kind: "daily", time: "09:00" }, timezone: "Asia/Shanghai", enabled: true, ends_on: null, paused_reason: null,
  conversation_id: "cnv_1", conversation_title: "新闻", due_at: null, next_run_at: null, last_run_at: null, last_run_id: null,
  last_conversation_id: null, last_status: null, last_error: null, ...patch,
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("schedule text", () => {
  it("says the rule the way people do", () => {
    expect(when({ kind: "weekly", time: "17:30", days: [4, 0] })).toBe("每周一、五 17:30");
    expect(when({ kind: "weekdays", time: "08:00" })).toBe("工作日 08:00");
    vi.useFakeTimers({ now: new Date(2026, 10, 11, 10) });
    expect(when({ kind: "once", time: "08:00", date: "2026-11-12" })).toBe("11月12日 08:00");
    expect(when({ kind: "once", time: "08:00", date: "2027-01-02" })).toBe("2027年1月2日 08:00");
  });

  it("dates a scheduled turn relative to today", () => {
    vi.useFakeTimers({ now: new Date(2026, 10, 12, 10) });
    expect(occurrenceText("2026-11-12 09:00 周四")).toBe("今天 09:00");
    expect(occurrenceText("2026-11-10 09:00 周二")).toBe("11月10日 09:00");
  });

  it("tells what the task is doing", () => {
    expect(stateText(task({ enabled: false, paused_reason: "done" }))).toBe("已完成");
    expect(stateText(task({ due_at: "2026-11-12T01:00:00Z", next_run_at: "2026-11-12T01:01:00Z" }))).toBe("等这条回答结束后执行");
    expect(stateText(task({ enabled: false }))).toBe("已停用");
  });
});

describe("scheduled turn", () => {
  it("is a time mark that opens to the task's instruction", () => {
    render(<ScheduledMark schedule={{ title: "早报", at: "2026-11-10 09:00 周二" }} prompt="汇总新闻" />);
    const mark = screen.getByRole("button", { name: /早报/ });
    expect(mark.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("汇总新闻")).toBeNull();
    fireEvent.click(mark);
    expect(mark.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("汇总新闻")).toBeTruthy();
  });
});
