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

1. `resolve_attachment` (file identity; panel open is planned:P3) opens the workbook in the panel and returns its sheets. Open it before discussing it so the user sees what you see.
2. `read_library_file` (file_id, anchor, next_cursor) returns the cells of a sheet or range as a grid and lists their formulas. Read the relevant range before answering; follow the `cursor` hint for large sheets instead of guessing.
3. `read_library_file` (file_id, query) finds text and returns cell anchors.
4. `planned:P3 show_in_viewer(targets, note?)` switches to the sheet, scrolls to the ranges and highlights them. Use it whenever your answer is about specific cells, e.g. the inputs that feed a total. Keep `note` to one short sentence.
5. `planned:P3 get_view()` tells you which sheet the user is looking at and which cells they selected, with the values the panel shows. Call it when the user says "this", "here", "these cells" or similar without an anchor.
6. `planned:P3 capture_view(anchor?)` (a panel tool, available while the panel is open) returns an image of the rendered sheet area. Use it when formatting, charts or the computed values matter. The workbook must already be open in the panel.

After you produce or change a workbook with the `xlsx` skill, open it with `planned:P3 open_document` so the user can look at the result with you.

## Values and formulas

`planned:P3 read_document` shows the values stored in the file and lists each cell's formula. A formula marked "not calculated in file" has no stored result (files written by scripts often lack them); the panel computes and shows a value for it. To see computed values, use `planned:P3 capture_view`, or ask the user to select the range and call `planned:P3 get_view`.

## References from the user

When the user selects cells and chooses **Add to chat**, the next message carries context like `- budget.xlsx#Revenue!B2:D4: "100 110 120…"`. Treat those anchors as precise pointers: read them with `planned:P3 read_document` if you need formulas or neighbouring cells, and answer about exactly those cells. **Ask** sends a question that already includes the anchor and the cell text.

`planned:P3 show_in_viewer` marks every target and shows your `note` beside the first one; the user can step through the targets in order, so list them in the order you discuss them.

## Notes

- Paths are relative to the workspace; files outside it cannot be opened. Only .xlsx opens, not .xlsm, .xls, .csv or .tsv; read those with the `xlsx` skill.
- Do not paste whole sheets into the chat; quote the few cells that matter and point with `planned:P3 show_in_viewer`.
- The source panel host is not available in AILab until P3/P4; use read_library_file and view_attachment today. Elsewhere, still open the document with `planned:P3 open_document`: the read and search tools work without the visual panel. Tell the user the visual viewer is unavailable.

AILab adaptation: panel open/reveal/get_view/capture and selection actions above describe preserved source semantics, planned:P3/P4, not currently callable tools. Use only returned file_id and anchors; do not fabricate path-based authorization.
