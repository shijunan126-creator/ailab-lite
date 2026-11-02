# 办公P1：模块与上下文基础（2026-11-02）

基线：`.worktrees/office-suite`，`feat/office-suite`，进入时干净 `5da37fa602526079f943d90598a1b70bb8aad604`；merge-base `origin/main@bf0af4f54b64f271772716c298e394ce1d6d78a4`，本轮未fetch。实现与测试代码提交 `8800615`，统一复核修复 `0a48b72`。本轮仅后端P1，无Portal、P2/P3、push、PR或部署。

## 实现与合同

- Chat/Work使用同一固定load_skill schema。无resource只给格式导航/目录；按manifest白名单读取指定cursor的完整分片，返回正文、分片/模块/包hash、token估算、行范围、依赖和next_cursor；未知名称、路径、cursor、模式不符、symlink及hash漂移均拒绝，不回退整包。生成导航撤掉P0的“读取待实施”提示，同步确定性构建与根index。
- 当前可见性只来自实际消息中的配对成功完整结果和正文hash；普通内置/文件技能也不再用loaded_skills拦截重读。重复读取只追加短结果，旧消息不变。增量派生索引只在进程内维护；显式上下文边界直接清除，即使压缩后列表长度和末尾对象恰好没变也重建；loaded_skills不阻止办公模块重读，安装包hash变化明确拒绝混读。
- 每次新正文用现有校准estimator/ModelPolicy核算messages、固定tools及已追加的同组结果，并为摘要调用/输出留余量；放不下明确返回cannot fit，不截断。P0的完整大围栏可读取，也可因小窗口明确拒绝。
- 轮内、跨轮摘要追加确定性模块身份索引，不摘要操作正文；无快照结构化摘要的read_modules保留导航身份，不能冒充正文可见性。原有用户输入、交付围栏、近期完整组及摘要CAS保持既有合同。没有历史工具正文或索引时不会猜测曾读模块，直接允许重读。
- 摘要模型错误发出工具调用时，追加配对的拒绝结果后重试，继续保持tool_choice=auto，不再切none；无快照重建不覆盖已有access_revoked原因。历史重建准入核算实际摘要指令/schema和输出余量，避免指令增长后压低原可用摘要输出。
- checkpoint保留原messages与固定工具；模块正文不复制到独立checkpoint字段。录制请求检查Chat/Work连续load、重读/错误、实际checkpoint恢复、两类压缩各连续三代（保留/丢弃正文）及无快照摘要重建；段内system/tools/tool_choice不变，压缩后有agent.context_boundary。

## 验证

当前检查与P0历史证据分开；运行输出/XML只在仓库外 `/private/tmp/office-suite-p1-*`，不入库。

| 检查 | 命令与结果 |
| --- | --- |
| Lint/format | `cd platform && .venv/bin/ruff check .`、`.venv/bin/ruff format --check .` 最终通过，516个文件格式一致；构建器用 `ruff check --config platform/pyproject.toml vendor/office-suite/build.py` 通过。 |
| 首轮专项 | `pytest tests/test_skill_modules.py tests/test_prefix_cache.py tests/test_office_suite_resources.py tests/test_base_office_skills.py tests/test_modes_skills_attachments.py -q`：108 passed、4 failed；自动导航重复追加及新测试的checkpoint包装/tool_choice断言修正后，仅复跑4项全部通过。 |
| 后追加专项 | 模块单测＋Chat/Work录制恢复21项：20 passed、1 failed（测试误把普通PDF分片当oversize）；改为从manifest选择真实不可拆大块，失败项单独复跑1 passed。资源重建/递归hash等21项已在首轮及全套通过。 |
| 完整Python（仅一次） | 在代码基线 `8800615` 执行 `.venv/bin/pytest tests -q --junitxml=/private/tmp/office-suite-p1-full.xml`：**2024 passed、162 skipped、10 failed**，359.5秒。未重跑全套。 |
| 全套失败与复核改动 | 导出仓库要求的GNU coreutils/findutils PATH；从上述XML精确选出10个失败node，加 `test_skill_modules.py`、`test_prefix_cache.py`、`test_personal_skills.py`、`test_base_office_skills.py`、`test_modes_skills_attachments.py`（可见性与摘要预算改动受影响）：**117 passed**。修复收录于 `0a48b72`；之后追加的摘要重试同形、撤权边界保留两项分别定点验证，**2 passed**。最终Ruff通过；未把此结果冒充最终提交重新跑过全套。 |

全套10个失败：8项因首轮漏导GNU工具PATH，mock沙箱使用macOS的stat/find导致快照/预览失败；只修正测试环境后均通过。1项200轮摘要测试暴露新增指令令原固定余量不足（输出984而非1000），已按真实摘要请求准入修复并通过。1项1000轮render生命周期内存断言超6MiB，定点复跑通过；没有为此改Broker或放宽断言，仍存在全套顺序/内存测量波动风险，单次复跑不代表稳定性证明。

162个skip主要是未配置PostgreSQL、Redis、Docker/OpenSandbox、S3、crawler等可选真实后端，不能据此宣称真实后端验收。额外在仓库根默认Ruff配置检查构建器曾报已有配置差异；改用项目Python配置后通过，未为此改无关代码。

## 成本与未测边界

- 无新依赖、常驻进程、数据库查询或沙箱启动。历史重建为每个候选轮次多做一次真实摘要请求估算（复用现有estimator），没有增加模型调用类型。导航正文估算Chat 378–380、Work 517–777 token（实现过程测量）；新结果增加小量身份元数据，整结果仍小于1024。每次按byte_offset读取单分片，原文不整包注入；可见性按消息增量扫描并只缓存身份，不再保存正文副本。
- 三类压缩录制证据属于结构合同，真实 `cached_tokens/prompt_tokens`、供应商≥95%目标、模型质量及CPU/RSS/时延实测属于P5，**本轮未做且不作为P1门禁**。结构测试不能声称供应商已命中缓存。
- Portal tests/build、浏览器、Redis/PostgreSQL真实后端、E2E、真模型、镜像/依赖运行闭包、视觉评审与全部制作路线未做；按本轮后端P1验证范围跳过，不能据资源可读宣称依赖或制作可用。
- 无快照且从未写入摘要的历史工具正文不可凭空恢复；按需重新加载是预期合同。摘要身份超出现有16000字符持久摘要容量会明确失败并保留游标，不悄悄丢弃身份。多包升级/撤权/工作区丢失、付费不确定结果等联合故障矩阵仍依既有合同，P5再做真实联合验收。

下一步：主任务评审本地P1提交；仅在主任务安排后进入P2/P3。本轮未发布、未部署。

## 产品合同修订（P2 Step 0）

用户最新决定取代本记录中的历史分页协议：`load_skill(name, resource?)` 按完整文件读取，移除 cursor/分片与打包上限。hash、模式白名单、当前消息配对去重、压缩后重读和 ModelPolicy 剩余预算拒绝保持。实施证据见 [P2](2026-11-02-office-suite-p2.md)。
