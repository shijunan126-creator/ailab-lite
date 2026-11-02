---
name: pdf-viewer
description: View and discuss PDF files with the user in the PDF side panel. Use when the user wants to open, read, review or ask about a PDF in the workspace, refers to something they selected or referenced in the PDF viewer, or should check a PDF you just produced.
---

# PDF viewer

The PDF panel renders a workspace PDF for the user. You read the same file through tools and point the user at places in it. The viewer is read-only: never claim to have edited a file through it. To produce or change PDFs, or to extract their content for other work, use the `pdf` skill.

## Anchors

Every location is an anchor `<workspace-relative path>#<location>`: `page:N` or `page:N-M`, for example `paper.pdf#page:5`. Omit `#<location>` for the whole document. Pages start at 1. Use only anchors that a tool returned or that follow this grammar; tool output prefixes every page with its location.

## Workflow

1. `resolve_attachment` (file identity; panel open is planned:P3) opens the file in the panel and returns its outline. Open the document before discussing it so the user sees what you see.
2. `read_library_file` (file_id, anchor, next_cursor) returns the text of a page or range. Read the relevant part before answering; follow the `cursor` hint for long content instead of guessing.
3. `read_library_file` (file_id, query) finds text and returns page anchors.
4. `planned:P3 show_in_viewer(targets, note?)` scrolls the panel to the targets and highlights them. Use it whenever your answer is about specific places. Add `quote` (exact text on the page) to highlight a phrase rather than the whole page. Keep `note` to one short sentence.
5. `planned:P3 get_view()` tells you what the user is looking at and what they selected. Call it when the user says "this", "here", "the selected …" or similar without an anchor.
6. `planned:P3 capture_view(anchor?)` (a panel tool, available while the panel is open) returns an image of the rendered page. Use it when layout, figures or formatting matter, or when text extraction comes back empty (for example scanned PDFs). The document must already be open in the panel.

After you produce a PDF with the `pdf` skill, open it with `planned:P3 open_document` so the user can look at the result with you.

## References from the user

When the user selects text or clicks a page and chooses **Add to chat**, the next message carries context like `- paper.pdf#page:4: "Latency fell by 40 percent…"`. Treat those anchors as precise pointers: read them with `planned:P3 read_document` if you need more than the quoted text, and answer about exactly those places. **Ask** sends a question that already includes the anchor and the quoted text.

`planned:P3 show_in_viewer` marks every target and shows your `note` beside the first one; the user can step through the targets in order, so list them in the order you discuss them.

## Notes

- Paths are relative to the workspace; files outside it cannot be opened. Encrypted PDFs cannot be opened.
- Do not paste whole documents into the chat; quote briefly and point with `planned:P3 show_in_viewer`.
- The source panel host is not available in AILab until P3/P4; use read_library_file and view_attachment today. Elsewhere, still open the document with `planned:P3 open_document`: the read and search tools work without the visual panel. Tell the user the visual viewer is unavailable.

AILab adaptation: panel open/reveal/get_view/capture and selection actions above describe preserved source semantics, planned:P3/P4, not currently callable tools. Use only returned file_id and anchors; do not fabricate path-based authorization.
