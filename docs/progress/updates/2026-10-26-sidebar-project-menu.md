# 2026-10-26 Sidebar project ⋯ menu

Base: `origin/main` @ 2233870. Branch: `feat/project-menu`.

- Sidebar projects had no ⋯ menu, so renaming, the brief and deletion were only reachable from the project page.
  Each sidebar project now has the same ⋯ as conversations: 重命名 (in place), 项目说明 (opens the brief editor on
  the project page), 删除项目 (same confirmation as the project page; leaves a page that no longer exists).
- Rename/delete logic moved to `lib/projects.ts` and is shared with the project page.
- Per user instruction, no lint/build/test/browser validation before the PR.
