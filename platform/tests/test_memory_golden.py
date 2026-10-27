"""The memory golden is a CI gate, with a no-memory retrieval baseline."""

from scripts.evaluate_memory_context import evaluate


def test_context_golden_has_no_scope_stale_or_irrelevant_injection():
    out = evaluate()
    assert out["required_recall"] == 1, out
    assert out["context_hurt_rate"] == 0, out
    assert out["no_memory_required_recall"] == 0
    assert not out["answer_quality_measured"]
