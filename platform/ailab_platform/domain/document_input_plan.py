"""Deterministic whole-attachment inline selection, independent of physical page admission."""


def choose_inline(items, input_budget, vision_tokens):
    text_budget = min(24000, input_budget // 5)
    chosen = {item["file_id"] for item in items if item["eligible"]}

    def exceeds():
        active = [item for item in items if item["file_id"] in chosen]
        return sum(item["text_tokens"] for item in active) > text_budget or sum(item["document_images"] for item in active) > 8

    while chosen and exceeds():
        largest = max(
            (item for item in items if item["file_id"] in chosen),
            key=lambda item: (item["text_tokens"] + item["images"] * vision_tokens, -item["ordinal"]),
        )
        chosen.remove(largest["file_id"])
    return chosen
