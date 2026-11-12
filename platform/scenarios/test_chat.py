"""Chat: words only, over several turns and over passages the user selected in a document."""

from scenarios.conftest import contracts
from scenarios.inputs import DOCX, quarterly_report


def test_multi_turn_conversation_keeps_its_prefix(api):
    cid, first = api.start("一个项目有 3 个阶段，分别需要 4、6、5 周，阶段之间串行。总共需要多少周？只回答数字和一句理由。", mode="chat")
    contracts(first)
    assert "15" in first.reply
    second = api.ask(cid, "如果第二阶段可以和第三阶段完全并行，总工期变成多少周？", mode="chat")
    contracts(second, later=True)
    assert "10" in second.reply
    third = api.ask(cid, "把这两种安排整理成一个两行的对比，列出每种的总周数。", mode="chat")
    contracts(third, later=True)
    assert "15" in third.reply and "10" in third.reply


def test_selected_passages_answer_the_question_and_keep_the_prefix(api):
    file = api.upload("经营简报.docx", quarterly_report(), DOCX)
    blocks = api.blocks(file["id"])
    profit = next(b for b in blocks["blocks"] if "净利润" in b["text"])
    risk = next(b for b in blocks["blocks"] if "续约" in b["text"])

    def ref(block):
        return {"file_id": file["id"], "version": blocks["version"], "at": block["at"], "quote": block["text"]}

    cid, first = api.start("选中的这段里，净利润是多少元？不算迁移费用的话是多少元？", mode="chat", doc_refs=[ref(profit)])
    contracts(first)
    assert "375000" in first.reply.replace(",", "") or "37.5" in first.reply
    assert "585000" in first.reply.replace(",", "") or "58.5" in first.reply
    second = api.ask(cid, "再看这段：哪件事最需要在 11 月推进？一句话。", mode="chat", doc_refs=[ref(risk)])
    contracts(second, later=True)
    assert "续约" in second.reply


def test_attached_document_answers_follow_ups(api):
    file = api.upload("经营简报.docx", quarterly_report(), DOCX)
    cid, first = api.start("这份简报里第三季度净利润是多少？一句话。", mode="chat", file_ids=[file["id"]])
    contracts(first)
    assert "37.5" in first.reply
    second = api.ask(cid, "第三部分提到了什么风险？一句话。", mode="chat")
    contracts(second, later=True)
    assert "续约" in second.reply or "到期" in second.reply
