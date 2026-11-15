"""Excerpt selection: deterministic, query-relevant paragraphs in page order."""

from ailab_platform.agent.web_excerpts import excerpt, terms


def test_english_terms_ignore_short_words_and_chinese_uses_bigrams():
    assert terms("The Rise of AI in 2026") == {"the", "rise", "of", "ai", "in", "2026"}
    assert terms("人工智能") == {"人工", "工智", "智能"}
    assert terms("猫") == {"猫"}
    assert terms("猫 疫苗") == {"猫", "疫苗"}


def test_the_most_relevant_paragraphs_are_joined_in_page_order():
    text = "\n\n".join(
        [
            "Cookie settings and privacy policy.",
            "Self-hosting a reader costs CPU only: self-hosting keeps the text on this machine.",
            "Unrelated advertising.",
            "A second paragraph about self-hosting and its costs.",
        ]
    )
    assert excerpt(text, "self hosting costs") == (
        "Self-hosting a reader costs CPU only: self-hosting keeps the text on this machine.\n\n"
        "A second paragraph about self-hosting and its costs."
    )


def test_a_page_without_a_matching_paragraph_has_no_excerpt():
    assert excerpt("Navigation\n\nFooter.", "机器学习") == ""


def test_the_excerpt_is_bounded_and_stable():
    paragraph = "机器学习" * 300
    first = excerpt(paragraph, "机器学习", max_chars=800)
    assert first == excerpt(paragraph, "机器学习", max_chars=800)
    assert len(first) <= 800
