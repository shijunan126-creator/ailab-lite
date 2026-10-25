"""Checkpointed active-work windows. A new durable continuation starts a fresh window."""

import time

from ..config import settings

REVIEW_NOTE = "（平台提示：已达到工作时间预算。请对照计划检查已完成的步骤和文件，判断继续工作还是交付已有成果，并向用户说明进度。）"
BUDGET_WRAP_NOTE = "（平台提示：本轮工作预算需要收尾。请停止扩展任务，保存并交付已有文件，说明未完成的计划；平台将暂停，用户可继续。）"


class BudgetPause(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class QuotaPause(BudgetPause):
    def __init__(self, window: str, resets_at: str):
        self.pause = {"kind": "quota", "window": window, "resets_at": resets_at}
        period = "今日" if window == "daily_tokens" else "本月"
        super().__init__(f"{period}额度已用完，已暂停，额度恢复后可继续")


class WorkBudget:
    def __init__(self, state, window_id):
        self.state = state
        if state.get("budget", {}).get("window_id") != window_id:
            if state.get("budget"):
                state["budget_note_pending"] = True
            state["budget"] = {
                "window_id": window_id,
                "active_seconds": 0.0,
                "review_at": settings.run_soft_budget_seconds,
                "review_progress": len(state.get("progress_marks", [])),
                "start_step": state["next_step"],
            }
            state["finished"] = False
        self.data = state["budget"]
        self.tick = time.monotonic()
        self.last_progress_at = self.tick
        self.suspended = False

    def update(self):
        now = time.monotonic()
        if not self.suspended:
            self.data["active_seconds"] += now - self.tick
        self.tick = now

    def progress(self):
        self.last_progress_at = time.monotonic()

    def boundary(self):
        self.update()
        elapsed = self.data["active_seconds"]
        wrapping = self.data.get("pause_step")
        if wrapping is not None:
            if self.state["next_step"] >= wrapping or self.state["finished"]:
                raise BudgetPause(self.data["reason"])
            return []
        if elapsed >= settings.run_max_seconds:
            return self.wrap(f"已工作{settings.run_max_seconds / 3600:g}小时，已暂停，可继续")
        if elapsed < self.data["review_at"]:
            return []
        progress = len(self.state.get("progress_marks", []))
        events = ["soft"]
        if progress > self.data["review_progress"]:
            self.data.update(review_at=elapsed + settings.run_review_interval_seconds, review_progress=progress)
            return [*events, "extended"]
        return [*events, *self.wrap("本轮暂无新的计划或文件进展，已暂停，可继续")]

    def wrap(self, reason):
        self.data.update(pause_step=self.state["next_step"] + 3, reason=reason)
        return ["wrap_up"]
