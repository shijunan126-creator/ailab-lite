---
name: visual-judge
description: "THE single visual acceptance pass for a rendered deliverable of these types only — pptx, docx, xlsx, pdf, poster, chart; for anything else, do not use it. Apply it once per deliverable, yourself, to the page images returned by `review_artifact` (or by `view_workspace_file` for pages you rendered to PNG); do not pre-screen the pages and then review them again, which duplicates the same review. Its bar is user acceptance: everything the user will see — visual asset quality and layout & composition — judged page by page as pass/fail with evidence-backed issues; a clean programmatic/script check is no cover and no substitute. Finish every verdict before changing anything, then fix the failed pages and check only those again."
---

AILab resource root: `/opt/skills/platform.xlsx`. Work shell: export SKILL_DIR="/opt/skills/platform.xlsx" XLSX_SKILL_DIR="/opt/skills/platform.xlsx" AILAB_SKILL_ROOT="/opt/skills/platform.xlsx"; outputs under /workspace/outputs.

These are the visual acceptance criteria for a rendered deliverable — a slide deck, a document (docx/pdf), a spreadsheet, a poster, or any artifact rendered to page images. Judge every page against them and give each a pass or fail verdict.

While judging, edit nothing: finish every page's verdict first, then repair.

## What you judge from

The page images returned by `review_artifact` or `view_workspace_file`, and the user's request. If something essential is missing or broken (no request, unreadable image), report it as `Unverified` in the output instead of guessing.

## What acceptance covers — everything the user will see

Check both on every page:

1. **Visual assets** — every image, chart, table and icon is on-topic, correct, and displayed at a natural aspect ratio without unintended stretching, squashing, deformation, or destructive cropping: a chart or table must show exactly what the surrounding content claims (right chart type, right values, nothing invented) and be cleanly drawn — sharp, unclipped (axes/legends/labels), no watermarks, no crude improvised graphics; stylized treatments are design choices, not defects.
2. **Layout & composition** — the page reads as finished work; report all of: modules overlapping each other, content stacked or hidden, elements spilling past the page or their container, modules crammed together, and visible imbalance (visual center off, one side overloaded while the other sits empty).

Office-scenario optimization — what each format needs specially:

- **pptx** — judge at presentation distance: each slide must land in one glance; watch for text colliding with or spilling off cards and shapes, a single card or container left half empty (that too is uneven visual distribution), chart labels too small to read when projected, and cross-slide consistency (page numbers, headers, palette).
- **docx** — judge at reading distance; watch for pagination artifacts (near-blank pages, headings orphaned at a page bottom, boxes broken across pages), TOC entries without page numbers, figures that rendered blank, and header/footer/page-number continuity across sections.
- **xlsx** — judge the rendered sheet views; watch for columns clipped to `####`, visible error values, charts whose type or labels misrepresent the data, wide tables sliced across print pages, and whether the dashboard reads as a whole.
- **pdf** — watch for content crowding or crossing the page margins, broken column flow in multi-column layouts, bad page breaks (a heading or caption stranded alone), and for posters and covers the first-glance impression.

## Workflow

Read your page images one by one, writing each page's verdict immediately after reading it.  re-rendering of any kind is forbidden. What you cannot confirm, mark `Unverified`. Then output — nothing after it.

## Reporting rules

- Report an issue only when you can state the concrete problem; name it.
- One issue, one category, one entry — if one root cause shows several symptoms on a page, report it once under the dominant category. Inside an image/chart/table is **Visual**; between elements on the page is **Design**; a violated brief item is **Spec** (quote the item; if no spec items were given, never invent constraints).
- Concrete evidence always: what you saw, or the source quote. No invented pixel values, no generic beautification advice.

## Output — one JSON line per page, in page order

```
{"page": 3, "verdict": "pass"}
{"page": 4, "verdict": "fail", "issues": [{"category": "Design", "problem": "chart overlaps the caption text below it", "evidence": "bar chart's bottom edge extends into the caption line on page 4"}]}
```

One line per page, passing pages included. Keep these verdicts in your own check; tell the user only what you fixed or could not verify. `category`: Spec | Visual | Design | Unverified; any criterion violated → fail; unconfirmed → `Unverified`.
