"""What a scheduled turn tells the model; shared by the Agent (current turn) and the API (history of earlier turns)."""


def scheduled_prompt(prompt: str, schedule: dict | None) -> str:
    """A scheduled turn is the platform speaking at the set time, not the user typing: say so, and when."""
    if not schedule:
        return prompt
    task = f"定时任务「{schedule.get('title', '')}」（{schedule.get('rule', '')}）"
    if schedule.get("manual"):
        # "立即运行": the user asked for one extra run now, it is not a repeated occurrence
        head = f"用户在 {schedule.get('at', '')} 手动立即运行了{task}，按它完成一次，不影响之后的定时。"
    else:
        head = f"{task}到点：{schedule.get('at', '')}。这条消息由平台按时发出，用户此刻可能不在：直接完成并把结果写在回复里，不要等待确认。"
    return "<scheduled_task>\n" + head + "\n</scheduled_task>\n" + prompt
