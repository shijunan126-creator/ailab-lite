"""Identity and aliases for the platform's verified, read-only skill catalog."""

BASE_SKILLS = ("platform.pdf", "platform.docx", "platform.xlsx", "platform.pptx")
ALIASES = {
    "xlsx": "platform.xlsx",
    "platform.document.read": "platform.docx",
    "platform.pdf.read": "platform.pdf",
    "platform.spreadsheet.read": "platform.xlsx",
    "platform.presentation.read": "platform.pptx",
}


def resolve_name(name: str) -> str:
    return ALIASES.get(name, name)


def kind(name: str) -> str:
    return "base" if name in BASE_SKILLS else "builtin"
