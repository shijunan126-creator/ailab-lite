"""Work across turns: revising a selected passage of its own deliverable, and sub-tasks only on request."""

from scenarios.conftest import contracts
from scenarios.inputs import docx_text


def test_revising_a_selected_passage_of_the_deliverable(api):
    cid, first = api.start(
        "写一份 Word 文档《选址建议》：一段背景，一段结论，结论段以“初步结论：”开头，建议选择 A 园区。交付 docx。", mode="work"
    )
    contracts(first)
    artifact, _ = api.artifact(first, ".docx")
    blocks = api.blocks(artifact["file_id"])
    conclusion = next(b for b in blocks["blocks"] if "初步结论" in b["text"])
    ref = {"file_id": artifact["file_id"], "version": blocks["version"], "at": conclusion["at"], "quote": conclusion["text"]}
    second = api.ask(cid, "把选中的结论段改成 B 园区，并改写成三条编号要点，其余内容不动，交付新版 docx。", mode="work", doc_refs=[ref])
    contracts(second, later=True)
    _, data = api.artifact(second, ".docx")
    text = docx_text(data)
    assert "B 园区" in text or "B园区" in text
    assert "背景" in text or any(b["text"] in text for b in blocks["blocks"] if "初步结论" not in b["text"] and b["text"].strip())


def test_parallel_work_stays_in_one_agent_unless_asked(api):
    task = "分别统计这三句话的汉字数：甲“春眠不觉晓”，乙“处处闻啼鸟”，丙“夜来风雨声”，汇总成 summary.md 交付。"
    _, plain = api.start(task, mode="work")
    contracts(plain)
    _, asked = api.start("请拆成三个子任务并行处理：" + task, mode="work")
    contracts(asked, subtasks=True)
