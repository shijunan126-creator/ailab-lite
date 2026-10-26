# 2026-10-26 Preview pane collapse and original-only web tabs

Base: `origin/main` @ 5218577. Branch: `fix/reader-original-only`.

## Done

- Side pane head button now collapses (`收起预览`) instead of discarding web/source tabs; the composer pill
  (`打开预览（n）`) appears whenever the pane is collapsed and holds anything (files, sources, web tabs) and
  restores it as it was. Before, web/source-only panes had no way back once closed.
- Web tabs show only the original page through the render proxy. Removed the 原版/正文 switch, the client-side
  extracted-article view (`ReaderText`/`BlocksView`), `reader()`/`ReaderBlock`/`ReaderPage` in `lib/api.ts`
  and their CSS. The ↗ open-in-new-tab link moved into the pane head.
- E2E `test_reader_tabs.py` / `test_large_conversation.py` updated: no 正文 tab; collapse/reopen asserted.

## Not done / validation

- Per user instruction, no lint, build, unit or browser validation was run before opening the PR.
- Backend `GET /v1/reader` (JSON extraction) is now unused by the Portal; left in place pending a decision.
