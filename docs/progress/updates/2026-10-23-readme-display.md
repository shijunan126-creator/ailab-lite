# 2026-10-23 · 恢复 GitHub 首页 README

用户反馈：首页看不到原始项目说明。原始根目录 `README.md` 未被删除；原因是 CI 更新新增 `.github/README.md`，GitHub 优先选它作为仓库首页说明。

本轮 live `GET /repos/shijunan126-creator/ailab-lite/readme` 返回 `path=.github/README.md`，与 [GitHub 官方优先级说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) 一致。此前检查只验证了文件内容与链接，遗漏了新增路径会影响首页展示。

修复：CI 说明改名为 `.github/CI.md`，同步契约文档链接和 CI 文档白名单；根目录 README 不修改。陷阱已记录到 HANDOFF。

基线 `origin/main@6caab9b`（PR #30 已由用户合并），分支 `fix/repository-readme`。此修复已提交 [PR #31](https://github.com/shijunan126-creator/ailab-lite/pull/31)；合并前 main 仍会展示旧的 `.github/README.md`。

验证：根 README 与基线逐字节一致；`.github/` 无 README 覆盖文件；CI scope 的 5 项测试、Ruff、actionlint、相对链接与 diff 检查通过。不涉及业务或 UI 源码，不重复完整后端/浏览器验收。合并后用 README API 确认返回根目录 `README.md`，完成线上展示验收。
