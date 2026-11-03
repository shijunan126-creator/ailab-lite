---
name: pdf-viewer
description: View and discuss PDF files with the user in the PDF side panel. Use when the user wants to open, read, review or ask about a PDF in the workspace, refers to something they selected or referenced in the PDF viewer, or should check a PDF you just produced.
---

# PDF viewer

The PDF panel renders a workspace PDF for the user. You read the same file through tools and point the user at places in it. The viewer is read-only: never claim to have edited a file through it. To produce or change PDFs, or to extract their content for other work, use the `pdf` skill.

## Anchors

Every location is an anchor `<workspace-relative path>#<location>`: `page:N` or `page:N-M`, for example `paper.pdf#page:5`. Omit `#<location>` for the whole document. Pages start at 1. Use only anchors that a tool returned or that follow this grammar; tool output prefixes every page with its location.

## Workflow

1. Use `resolve_attachment` to identify an attached library file, then `document_view` with `op="open"` and its returned `file_id` to request the side panel. This returns `sent`, not proof that the user has seen it. Read the outline with `read_library_file`.
2. `read_library_file` (file_id, anchor, next_cursor) returns the text of a page or range. Read the relevant part before answering; follow the `cursor` hint for long content instead of guessing.
3. `read_library_file` (file_id, query) finds text and returns page anchors.
4. `document_view(op="reveal")` scrolls the panel to the targets and highlights them. Use it whenever your answer is about specific places. Add `quote` (exact text on the page) to highlight a phrase rather than the whole page. Keep `note` to one short sentence.
5. `document_view(op="get_view")` tells you what the user is looking at and what they selected. Call it when the user says "this", "here", "the selected …" or similar without an anchor.
6. `document_view(op="capture", source="viewport")` (a panel tool, available while the panel is open) returns an image of the rendered page. Use it when layout, figures or formatting matter, or when text extraction comes back empty (for example scanned PDFs). The document must already be open in the panel.

After you produce a PDF with the `pdf` skill, open it with `document_view(op="open")` so the user can look at the result with you.

## References from the user

When the user selects text or clicks a page and chooses **Add to chat**, the next message carries context like `- paper.pdf#page:4: "Latency fell by 40 percent…"`. Treat those anchors as precise pointers: read them with `read_library_file` if you need more than the quoted text, and answer about exactly those places. **Ask** sends a question that already includes the anchor and the quoted text.

`document_view(op="reveal")` marks every target and shows your `note` beside the first one; the user can step through the targets in order, so list them in the order you discuss them.

## Notes

- Paths are relative to the workspace; files outside it cannot be opened. Encrypted PDFs cannot be opened.
- Do not paste whole documents into the chat; quote briefly and point with `document_view(op="reveal")`.
- A missing, expired or ambiguous current view returns `unavailable` or candidates. Ask which document/range the user means; do not guess a current selection or silently substitute the first page. Reading a known library file remains possible without an online viewer.

## AILab tool contract

- The shared Chat/Work tool is `document_view`, with `op` equal to `open`, `reveal`, `get_view` or `capture`. There are no separate open_document, show_in_viewer, get_view, capture_view or read_document tools.
- For a library file, open/reveal/capture require the returned `file_id`. For an active Work workspace file, use its authorized `path` instead; never invent a library path from the filename. Use one target identity, not both. Reading workspace content in Work uses `office-read`; Chat cannot execute scripts.
- A locator such as `p:2`, `Revenue!A1:C3`, `slide:1/shape:2` or `page:1` goes in `at`, without the display path or `#` prefix. Use only locations returned for that document's version.
- Reveal puts locations and optional exact text inside `targets`: `{"op":"reveal","file_id":"RETURNED_FILE_ID","targets":[{"at":"RETURNED_LOCATOR","quote":"EXACT_SOURCE_TEXT"}]}`. Do not put quote at the top level or pass filename. `sent` only confirms that the command was sent.
- When the user says “here”, “this” or “the selected text” without an explicit reference, call `document_view` with `{"op":"get_view"}` first. Read the returned file/path, version, at and selection. With multiple views, disambiguate rather than choosing one arbitrarily. Ordinary composer messages do not automatically include the current selection.
- Add to chat appends an exact versioned reference to the composer; Ask sends the question with that reference immediately. These explicit message references are frozen and take priority over later viewer state. For Excel, quoted formula cells may contain source formulas rather than computed display values.
- To inspect the actual visible layout, call `document_view` with `op="capture"`, the returned file_id or workspace path, `view_id` from get_view, and `source="viewport"`. Keep the viewer open; the call may return unavailable. Only vision-capable models can inspect the returned image; text-only results do not prove visual quality.
- `source="render"` requests a specified `at` through bounded server rendering; this is not the user's current viewport. Some structural locations have no proven page mapping and explicitly return unavailable. Never claim to have seen an image that was not returned.
- The viewer is read-only. Make edits in Work, preserve the original, and open the new version after delivery. Do not silently treat an old reference as a newer file version.
