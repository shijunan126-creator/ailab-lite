# 历史交付件预览恢复

## 范围与根因

用户先授权本地修复，随后要求提交、提 PR 并部署本机，再要求 CI 通过后合并。分支 `fix/legacy-artifact-preview`，本地测试基线为 `main@ef76d55a` 加本轮产品变更，已固化为 `1927ad78`。[PR #192](https://github.com/shijunan126-creator/ailab-lite/pull/192) 完整 CI 以最终 head 为准，发布时不重复已完成的检查。

#161（`2bf86144`）之前的交付代码已经扫描，但只保存文件 `ready`，缺少扫描结果。新版派生文件写入要求 `scan.verdict=clean`，而历史数据没有恢复路径；预览任务渲染完成后保存图片才被拒绝。公有预览接口和排队入口又永久复用失败缓存，单独更新新文件写入无法恢复这些旧文件。

## 修复

- 仅对 `source=sandbox`、`ready` 且没有扫描判定的旧交付件，在预览请求事务内转为 `pending` 并写入现有 `file` outbox 任务。恢复标记区分它与普通上传，轮询仍返回预览 `pending`。
- Worker 使用现有有界下载、扫描和重试；不提前预热渲染，不重复解析文档。扫描通过后流式校验 SHA-256 和大小，通过已有 claim 围栏保存真实扫描结果。
- API 在同一事务恢复 `ready`、清除旧失败预览状态并写入新的预览 outbox；重扫期间重复打开不新增任务，失败缓存可恢复。
- 病毒或内容变化拒绝文件；扫描临时不可用走原重试，耗尽或 job deadline 到期使预览失败，不永远停在等待。

## 验证

在上述本地产品代码基线运行：

- 改动的 6 个 Python 文件通过 `ruff check` 与 `ruff format --check`。
- 从 `platform/` 运行 `.venv/bin/pytest -o addopts='' tests/test_file_previews.py tests/test_eager_previews.py tests/test_preview_partial.py tests/test_preview_priority.py tests/test_scanner_isolation.py tests/test_artifacts.py tests/test_derived_assets.py tests/test_file_work_completion.py tests/test_transactional_jobs.py tests/faults/test_transactional_jobs.py -q --tb=short --show-capture=no --disable-warnings`：`147 passed / 16 skipped`，19.96 秒。跳过项为环境条件测试，不算已验证。
- 7 个新增回归覆盖旧 PPT/文本恢复到真实 API 派生资源发布、并发打开与旧 claim 拒绝、短暂扫描故障恢复、扫描耗尽/病毒/摘要不符和任务过期。扫描器及渲染器使用既有测试替身，不代表真实 ClamAV/OpenSandbox 验收。

首轮新增用例因测试没有认领新扫描任务被 `stale_file_attempt` 拒绝；已改为真实调用 claim 接口，没有放松产品围栏。

## CI 中的既有队列测试竞态

`6e077a49` 的 PostgreSQL CI：`3193 passed / 91 skipped / 1 failed`，唯一失败为 `test_reserved_render_slot_survives_full_general_pool_and_queue[memory]`；聚合 `CI` 因此失败，其他所选检查均成功。

用例用真实线程排入仅存活 0.2 秒的票据，然后创建渲染沙箱、检查第二次通用请求必须收到 `queue_full`。CI 调度停顿足以让原票据先过期，因此第二次请求进入空队列、到期收到 `queue_deadline`。这是测试场景未固定，不涉及预览产品代码。

改为测试局部的受控单调时钟，直接通过真实 admission 占满队列，再通过真实 LeaseManager 验证保留渲染槽可用、通用请求严格拒绝 `queue_full`，最后推进同一票据期限断言 `queue_deadline` 和移除。没有放宽断言、增加 sleep、重试或期限；原 FIFO/线程等待测试仍保留。

本地 `ruff check`、`ruff format --check` 通过；故障模块 `tests/faults/test_broker_recovery.py` 使用隔离内存及新建的临时 Redis（随机非默认端口）全部 `60 passed`，5.92 秒。测试后 Redis 进程和临时目录已清理；未重复完整本地套件，等待最终 CI。

## 最终 CI 与合并

2026-11-13，最终 head `ba70461e649edd333755a3f0c325eb27c4cd6a16` 的全部 11 项检查通过（包括 SQLite、PostgreSQL、3 组 E2E 与聚合 `CI`），无评审或行内评论阻塞。按用户授权 squash 合并为 `871fa1147ce29b8d1ca55f23ed56938b7c066ba4`；`git diff --exit-code ba70461e origin/main` 验证合并树一致。

本节和当前交接为合并后的纯文档更新，不是额外产品修改；不重复本地测试或部署。实际运行代码与合并版本一致，本机镜像来源仍保留原部署提交。

## 成本与边界

无需迁移、配置或依赖变更；仅在访问缺记录的历史文件时额外扫描一次。API 不读取原文件；Worker 复用上传大小限制、下载临时文件和扫描期限，SHA-256 使用有界缓冲流式读取；额外磁盘读为一次摘要计算，不在内存保存整份文件。已带扫描结果的新文件不进入恢复。

本地未测真实运行栈的 ClamAV/OpenSandbox 联合恢复、PostgreSQL、完整 CI 和大文件峰值资源；完整检查交 CI 执行。未更改提示词、模型请求、Portal 或沙箱镜像，无本轮真模型或浏览器检查。原故障文件未手动改数据，部署 API 与 Worker 后可通过重新打开触发恢复；本机部署结果只记私有环境交接。
