"""Versioned document capability policy, shared by Agent and API admission."""

LEGACY_DOCUMENT_TOOLS = frozenset({"read_library_file", "extract_text", "view_attachment", "document_view"})
CHAT_DOCUMENT_TOOLS = frozenset({"read_document", "search_document", "view_document"})


def catalog_version(options):
    return 2 if (options or {}).get("document_tools_version") == 2 else 1


def permitted(name, *, mode="work", version=1):
    if name == "ocr_workspace_image":
        return version == 2 and mode == "work"
    if version == 1:
        return name not in CHAT_DOCUMENT_TOOLS
    if name in LEGACY_DOCUMENT_TOOLS:
        return False
    if name in CHAT_DOCUMENT_TOOLS:
        return mode == "chat"
    return True
