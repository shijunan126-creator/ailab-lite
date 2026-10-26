# 2026-10-26 Sidebar project ⋯ menu

Base: `origin/main` @ 2233870. Branch: `feat/project-menu`.

- Sidebar projects had no ⋯ menu, so renaming, the brief and deletion were only reachable from the project page.
  Each sidebar project now has the same ⋯ as conversations: 重命名 (in place), 项目说明 (opens the brief editor on
  the project page), 删除项目 (same confirmation as the project page; leaves a page that no longer exists).
- Rename/delete logic moved to `lib/projects.ts` and is shared with the project page.
- Per user instruction, no lint/build/test/browser validation before the PR.

## Follow-up investigation and merge authorization

User authorized both PRs to be pushed, made green and squash-merged, with no deployment. Worktree `/private/tmp/ailab-reader-original`, original head `4743fcda169eb078dd7c5056840f70916e608863`; merged `origin/main@ddaf756` as `ae0d1e9`. Main checkout and personal production directories are untouched.

### Causes and fixes

- [CI 40306991497 attempt 1](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40306991497/attempts/1): 110 passed / 1 skipped / 1 failed. The trace shows queue POST **201**, concurrent GET returning `queue: []`, then the queued row disappears. `chat.tsx` was byte-identical to main (`dc8588cae68f45d0300b25c9fa3e1c06b2493d86` SHA-1). This is a pre-existing read/write race, not caused by project-row layout/focus or the new portal menu.
- New deterministic browser regression holds a real detail response until the queued row is visibly confirmed; releasing that response reliably fails the original code at the second visibility assertion. A local write version now makes stale detail responses re-read the current snapshot. It preserves the refresh caller's continuation (including following an auto-dispatched turn). Queue add/edit/delete, ordinary send and answer/resume all mark confirmed writes; no polling, timeout or assertion weakening.
- The new desktop/mobile menu tests exposed a separate #93 defect: GET /conversations defaults to unassigned conversations, so the sidebar list cannot identify an open conversation belonging to the deleted project. After deleting a project, perform one bounded limit=1 existence check and leave only a 404 conversation; ignore the result after a route change. The tests exercise portalling, inline rename, brief edit and deletion/navigation on both viewports. Both failed before this correction.
- A full local E2E run (113 collected, 111 passed / 1 skipped / 1 failure) exposed the older PPT test requiring `.viewer img` although rendering correctly opened the live sidebar. It now verifies the unique actual preview surface, a visible fully loaded raster image with positive natural dimensions, or a PDF canvas with nonblank rendered pixels. An initial follow-up test run also caught a missing local test import; corrected before the final grouped run.
- CI attempt 2 timed out because Chromium system-dependency setup consumed 11m25s. Its log shows the Azure Ubuntu HTTP mirror fetching 25.9 MB in 10m49s at 40.0 kB/s; browser tests started at 10:58:13Z with under three minutes left in the 15-minute job. This was setup starvation, separate from the queue race. The same setup delay recurred on PR94 run 40314377906. The E2E job now replaces that Azure HTTP entry with the official Ubuntu HTTPS archive before installing dependencies; tests, skips and the job deadline are unchanged.

### Validation and boundary

- Local environment attempts before execution: the main checkout's existing node_modules lacked react-pdf; a fresh `npm ci` was used in this worktree. Its existing venv lacked rapidfuzz; a separate `/tmp/ailab-pr-verify-venv` was installed from this branch family's locked dependencies. These setup failures are not product failures; main checkout files were not changed.
- Current code baseline: `4743fcd` + main `ddaf756` + this follow-up diff. Portal Node tests 42/42 and production build/TypeScript pass through the E2E fixture. Ruff check and format pass. Final grouped browser results are recorded below; full exact-head dual-DB/storage/Portal/E2E checks are required before merge.
- Cost: an extra detail GET occurs only if a confirmed write crossed an already-running read; its result remains capped at 20 turns. Project deletion uses one limit=1 existence read. No new dependency/background poll or database migration. Target-machine latency was not measured.
- No deployment, production access, paid model calls or real Docker/OpenSandbox acceptance. Mock-stack browser acceptance does not imply those boundaries are tested. Final head/merge state is available from [PR #93](https://github.com/shijunan126-creator/ailab-lite/pull/93).

Final grouped browser verification: **13 passed / 0 failed / 0 skipped**, including the complete workspace feature module (desktop/mobile menus, queue stale-response, queue edit/remove/auto-dispatch, steer, paused queue) and PPT preview. Tested the final product diff plus `ddaf756`; the fixture rebuilt the Portal and ran 42 Node tests successfully. Full Python suites and full 115-case E2E remain the exact-head CI gate.

CI infrastructure follow-up: head `318c66d` started full suites, but slow Azure apt downloads were still consuming the E2E budget. The subsequent CI-only commit changes the dependency mirror, with final validation required on its own exact head. Local final grouped browser result remains 13/13.
