"""Plain-text normalization shared by all automatic title paths."""

import re


def normalize_title(candidate: str) -> str:
    """Store the same bounded plain text regardless of the model/title delivery path."""
    candidate = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", candidate)
    candidate = re.sub(r"^\s*#+\s*", "", candidate)
    candidate = re.sub(r"[*_`]+", "", candidate)
    return " ".join(candidate.split()).strip("\"'“”")[:40]
