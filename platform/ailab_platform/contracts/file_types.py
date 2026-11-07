"""File kinds understood by bounded native text preparation."""

OFFICE_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})


def legacy_scanned_pending(status, meta):
    """Only pre-migration jobs may derive between scan and parser completion."""
    return status == "pending" and meta.get("input_lifecycle") != 2 and (meta.get("scan") or {}).get("verdict") == "clean"


TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".log",
        ".py",
        ".sh",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".sql",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".toml",
        ".ini",
    }
)

DETECTED_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "zip": "application/zip",
}
