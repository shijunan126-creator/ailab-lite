# 单机全量代码审查与修复交付（2026-10-25）

## 目标、授权与代码基线

用户明确要求完整 code review，按单机体验、调度、性能、可靠性、安全和可维护性直接修复，完成端到端验证、提 PR 并合并，不用再次确认。本轮不部署，不改变个人模型配置/凭据/运行数据，不引入多机组件。

分支 `fix/single-node-review`，base `origin/main@df30dd5`；起点 `aadfa9a` 是前轮合并文档，不含未集成产品代码。实现提交：`94e1d0f`（恢复预算/部门快照/0029）、`34db4cd`（详情批量读取/历史版本查找/完整插话）、`869e06b`（Portal异步生命周期及浏览器回归）。`e23cf4d` 仅修正新增浏览器测试的等待选择器；产品树与 `869e06b` 一致。

## 审查结果

九类确认问题均已修复；原因、行为差异、回归与资源成本集中见[审查报告](../../reviews/2026-10-25-single-node-review.md)。没有通过放大并发、提高内存上限或添加基础组件替代根因修复。

性能证据：20轮详情读取86→9次；500轮首屏本机SQLite/PG p95 20.13/20.91ms；受控慢Work+100 Chat调度排队p95 33.66ms/活跃峰值3；十万行SQLite部门快照迁移0.532s。以上是本机短时/合成负载，不是目标机生产容量。

## 本轮验证

- PG16+真实Redis最终完整套件：1216 passed / 33 skipped，包含双库迁移、跨进程并发/故障与查询计划；未配置外部组件跳过，原因见审查报告。
- SQLite+真实Redis最终完整套件：**1170 passed / 79 skipped，覆盖率87.04%**；独立SQLite/PG分页与EXPLAIN各11项通过。
- Portal：35项原生Node测试和生产build通过；完整五服务E2E首轮96通过。补历史引用和手机视口后97通过/1项夹具选择器失败；选择器修正后的最终五服务全套 **98 passed / 0 failed / 0 skipped**。
- Ruff check/format、服务生产入口导入、技能目录manifest/哈希、golden自测、依赖锁、28项CI脚本单测通过；ShellCheck、bash语法、dev/lite/sandbox三套Kustomize静态渲染通过。

本轮命令入口（数据库只传专用可丢弃实例）：

```bash
cd platform
# macOS先把GNU coreutils/findutils放到PATH开头。
AILAB_TEST_REDIS_URL=redis://127.0.0.1:<isolated-port>/12 .venv/bin/pytest tests -ra --cov=ailab_platform
AILAB_TEST_REDIS_URL=redis://127.0.0.1:<isolated-port>/15 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:<isolated-port>/review \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:<isolated-port>/migrations .venv/bin/pytest tests -ra
AILAB_E2E=1 AILAB_E2E_BASE_PORT=<isolated-base> AILAB_E2E_PORTAL_PORT=<isolated-portal> \
AILAB_TEST_REDIS_URL=redis://127.0.0.1:<isolated-port>/13 .venv/bin/pytest e2e -ra
PYTHONPATH=. .venv/bin/python tests/load/agent_scheduler.py --work-seconds 10
```

## 发布与残余边界

[PR #49](https://github.com/shijunan126-creator/ailab-lite/pull/49)已创建并登记；初始head `465eec4`，后续本段链接提交仅文档。最新head `0dc288433f15916dcabc21ee6d987fd5a51b8bb8` 的八项检查全部通过，无阻塞审查或冲突；已于2026-10-25 00:54（UTC+8）squash合入main `d11f86ee2b40d3e9a0224358e4389f37b81fe357`，树与测试head一致。已确认E2E五服务与浏览器退出，内置preview tab关闭；停止本任务专用PG/Redis，核对进程归属，25495/26495/38190–38193/33110均无监听。未停止原有服务；临时原始日志仅在仓库外。运行环境仍为部署记录 `b2c8394`；0029只在隔离测试库执行。本机外部存储链路、24小时稳定性、真机键盘、真实Docker/OSB/S3/ClamAV/Keycloak与付费模型联合验收不在本轮结论内；真实S3独立契约已由该head的远端CI通过；这不等于真实组件全栈联合验收。个人配置与原有服务未动，三份`.DS_Store`保留。

## 远端首轮失败与修复

PR head `f6016805ca80aa5da49f8a1aa0c935d676397764` 的 [CI 40112028351](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40112028351)：PG、五服务E2E、真实S3契约、quality/shell与范围判断通过；SQLite只有500轮首屏性能分项失败，进而使聚合CI失败。日志是p95=156.32ms、max=209.30ms，其余24/25请求约25–33ms；没有业务断言/覆盖率失败。

原因是测试用25次样本的`statistics.quantiles`默认exclusive插值，p95落在第24与第25个值之间、权重70%落在最大值；这个定义不能直接代表“至少95%的实测请求低于150ms”。现在保持150ms门槛，不skip、不加失败重试、不提高机器预算，改为100次采样并取实测nearest-rank p95，同时保留max用于暴露尖峰。

修复仅改变性能测试和交接文档，产品代码仍为`869e06b`；双库性能专项各11项通过，SQLite覆盖率下100次p95=18.31ms/max20.13ms，PG p95=16.40ms/max18.87ms；SQLite完整套件1170通过/79跳过，覆盖率87.04%。专用PG/Redis再次核对归属后停止。本段测试工作树为f601680加本次测试/文档diff，产品树仍869e06b；测试文件哈希见validation.json。最新head八项门禁已全部通过并合并，见上方精确SHA；首轮失败不被后续绿结果抹去。


## 合并交接

本段只记录已完成的PR #49合并结果，不改变产品代码/部署。文档分支`docs/single-node-review-merged`从`origin/main@d11f86e`起，文档链接与状态核对后通过独立文档PR集成；没有再次执行数据库迁移或发布镜像。最终工作仍受“未部署”的边界约束。
