---
name: xlsx-viewer
description: View and discuss Excel (.xlsx) workbooks with the user in the Spreadsheets side panel. Use when the user wants to open, look through or ask about a workbook in the workspace together, refers to cells they selected or referenced in the Spreadsheets viewer, or should check a workbook you just produced.
---

# Excel viewer

The Spreadsheets panel renders a workspace workbook for the user. You read the same file through tools and point the user at cells in it. The viewer is read-only: never claim to have edited a file through it. To create, change or analyse workbooks, or to extract their data for other work, use the `xlsx` skill.

## Anchors

Every location is an anchor `<workspace-relative path>#<location>`:

- `Sheet` a whole sheet
- `Sheet!A1` one cell, `Sheet!A1:D20` a range

Quote sheet names that contain spaces: `model.xlsx#'Q3 data'!B2:F9`. Omit `#<location>` for the whole workbook. Use only anchors that a tool returned or that follow this grammar; tool output prefixes every block with its location.

## Workflow

1. Use `resolve_attachment` to identify an attached library file, then `document_view` with `op="open"` and its returned `file_id` to request the side panel. This returns `sent`, not proof that the user has seen it. Read the outline with `read_library_file`.
2. `read_library_file` (file_id, anchor, next_cursor) returns the cells of a sheet or range as a grid and lists their formulas. Read the relevant range before answering; follow the `cursor` hint for large sheets instead of guessing.
3. `read_library_file` (file_id, query) finds text and returns cell anchors.
4. `document_view(op="reveal")` switches to the sheet, scrolls to the ranges and highlights them. Use it whenever your answer is about specific cells, e.g. the inputs that feed a total. Keep `note` to one short sentence.
5. `document_view(op="get_view")` tells you which sheet the user is looking at and which cells they selected, with the values the panel shows. Call it when the user says "this", "here", "these cells" or similar without an anchor.
6. `document_view(op="capture", source="viewport")` (a panel tool, available while the panel is open) returns an image of the rendered sheet area. Use it when formatting, charts or the computed values matter. The workbook must already be open in the panel.

After you produce or change a workbook with the `xlsx` skill, open it with `document_view(op="open")` so the user can look at the result with you.

## Values and formulas

`read_library_file` shows the values stored in the file and lists each cell's formula. A formula marked "not calculated in file" has no stored result (files written by scripts often lack them); the panel computes and shows a value for it. To see computed values, use `document_view(op="capture", source="viewport")`, or ask the user to select the range and call `document_view(op="get_view")`.

## References from the user

When the user selects cells and chooses **Add to chat**, the next message carries context like `- budget.xlsx#Revenue!B2:D4: "100 110 120…"`. Treat those anchors as precise pointers: read them with `read_library_file` if you need formulas or neighbouring cells, and answer about exactly those cells. **Ask** sends a question that already includes the anchor and the cell text.

`document_view(op="reveal")` marks every target and shows your `note` beside the first one; the user can step through the targets in order, so list them in the order you discuss them.

## Notes

- Paths are relative to the workspace; files outside it cannot be opened. Only .xlsx opens, not .xlsm, .xls, .csv or .tsv; read those with the `xlsx` skill.
- Do not paste whole sheets into the chat; quote the few cells that matter and point with `document_view(op="reveal")`.
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
