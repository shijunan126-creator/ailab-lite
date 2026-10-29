# 当前工作

## 2026-10-29 main 同步与 CI 回归修复

用户要求同步远端 main 并修复最近三次提交的 CI。已将本地 main 快进到 `origin/main@1d07d7d`；主目录 `fix/full-thinking-rendering@ab0e8d9` 的既有脏修改保留，修复隔离在 `.worktrees/ci-main-fix`、分支 `fix/main-ci-regressions`，base `1d07d7d`。#102 的记忆保存 E2E 提前刷新和 #103 的匿名登录重写 HTTP 500 已定位并修复，Python 1481 passed/104 skipped、浏览器 E2E 126 passed、Portal 44/build 和 Ruff 通过，详见[本轮记录](updates/2026-10-29-main-ci-fix.md)。用户已明确授权直接修复并 merge；修复见 [PR #105](https://github.com/shijunan126-creator/ailab-lite/pull/105)，下一步等待最终 head 的完整 CI，绿灯后合并并同步本地 main。不部署，不延续其他设计实施授权。

## 2026-10-29 测试环境部署约定合入 main

用户要求将后续约定合入 main。本轮分支 `docs/test-deployment-policy`，基线 `origin/main@9b7449d`，保留 #103 的文档精简规则；仅补充 AGENTS.md、HANDOFF 和部署交接，不改产品或部署配置。新规则为：当次明确要求才部署，未要求不备份、不另做部署后验收，复用依赖并按变化范围增量构建。

main@5eef026 已在上一轮重新部署，旧版及备份已清理；后续功能验收按用户要求取消。本次不重新部署，也不运行产品测试，只检查文档差异和引用。[部署与约定记录](updates/2026-10-29-main-redeploy-cleanup.md)。

文件上下文功能已随 [PR #102](https://github.com/shijunan126-creator/ailab-lite/pull/102) 合并；NPU、真实多代满窗与数百人容量仍未验。原已丢失关系的旧分支需重新 fork。[交付记录](updates/2026-10-29-file-context-pr.md)。

主目录前端 HLD/视觉设计仍为本地设计，不授权实施。文档证据按 [#103 精简记录](updates/2026-10-29-docs-cleanup.md) 保留最小必要内容。下一步合并本次文档后等待用户继续指示，不自动扩展实施、测试或部署。
