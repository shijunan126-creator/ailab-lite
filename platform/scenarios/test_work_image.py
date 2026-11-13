"""Work: reading text that exists only as pixels in an uploaded image."""

from scenarios.conftest import contracts
from scenarios.inputs import receipt_png


def test_receipt_number_and_total_are_read_from_the_image(api):
    file = api.upload("receipt.png", receipt_png(), "image/png")
    _, turn = api.start("这张收据的编号和总金额是多少？只回答这两项。", mode="work", file_ids=[file["id"]])
    contracts(turn)
    assert "2026-0457" in turn.reply and "3280.5" in turn.reply.replace(",", ""), turn.reply
