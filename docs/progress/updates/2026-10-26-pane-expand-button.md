# 2026-10-26 Preview pane expand button in the top-right corner

Base: `origin/main` @ 78a69f8. Branch: `fix/pane-expand-button`.

- After collapsing the side pane the only way back was a pill above the composer, far from where the pane
  lives. The control moved to the chat's top-right corner as an icon button (`打开预览（n）`, title 展开预览),
  mirroring the nav's 展开侧栏 button at the top-left; the badge counts the tabs it brings back.
- `GET /v1/reader` (JSON extraction) is not used by the model: the agent reads pages through `web.read_url`.
  Since PR87 nothing calls it; left in place pending a backend cleanup decision.
- Per user instruction, no lint/build/test/browser validation before the PR.
