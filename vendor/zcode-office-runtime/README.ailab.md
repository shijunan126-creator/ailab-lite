# AILab Office reader

Pinned upstream: ZCode `3c5b227649371507ddc4eb46ac5133afe6db1cab`.
`UPSTREAM.json` records original file hashes and local adaptations. Runtime source is MIT (`LICENSE`); viewer skills are Apache-2.0 (`APACHE-2.0`). No proprietary generation skills are imported.

`npm ci --ignore-scripts && npm run build && npm test` builds the parser-only runner. The product runner is installed in `images/sbx-document-reader` and `images/sbx-office`; the business image has no Node parser. Runtime dependencies are ExcelJS, fast-xml-parser, JSZip and PDF.js; UI/server sources are retained for provenance but are not reachable from `runner.ts`. Development test/build dependencies are pruned from the production stage. Original package metadata is in `upstream-package.json`.

The AILab adapter traverses explicit PDF pages/PPT slides/Word blocks/complete Excel sheets and persists bounded text chunks. The local pagination patch preserves oversized unit continuations. This does not make the underlying parsers streaming: input/model copies still exist, and the Broker system sandbox enforces memory/CPU/network/time/disk limits. Worker validates the bounded output archive. Unsupported or resource-limited input fails explicitly; no silent old-parser fallback.

Work: build from the repository root with `docker build -f images/sbx-office/Dockerfile -t sbx-office:lite .`. Run `office-read /workspace/inputs/example.docx /workspace/work/check-1` with a nonexistent output directory. Inspect `manifest.json` and numbered text chunks; after editing use a new directory. The CLI has a 60s deadline and 160 MiB V8 heap ceiling (not a total RSS limit); sandbox cgroups remain the resource boundary. It starts no resident server. Local output is not a platform-authorized file index. Existing Python libraries perform edits; LibreOffice/Poppler render or convert; OCR stays in the platform executor.
