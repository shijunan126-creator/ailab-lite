"""Where a document-reading tool may be called: Chat reads library documents, Work reads its sandbox."""

CHAT_DOCUMENT_TOOLS = frozenset({"read_document", "search_document", "view_document"})


def permitted(name, *, mode):
    return mode == "chat" if name in CHAT_DOCUMENT_TOOLS else True
