"""What a scheduled turn tells the model; shared by the Agent (current turn) and the API (history of earlier turns)."""


def scheduled_prompt(prompt: str, schedule: dict | None) -> str:
    """A scheduled turn is the platform speaking at the set time, not the user typing: say so, and when."""
    if not schedule:
        return prompt
    return (
        "<scheduled_task>\n"
        f"定时任务「{schedule.get('title', '')}」（{schedule.get('rule', '')}）到点：{schedule.get('at', '')}。"
        "这条消息由平台按时发出，用户此刻可能不在：直接完成并把结果写在回复里，不要等待确认。\n"
        "</scheduled_task>\n" + prompt
    )
