# 测试

测试覆盖 Python 服务、Portal、浏览器端到端、真实组件契约和真模型场景。它规定改动如何被证明，不负责部署与运行环境；部署验收见[部署](deploy.md)。

## 测试地图

| 要测什么 | 放在哪里 | 怎么跑 |
|---|---|---|
| 业务单元、API 契约、恢复逻辑 | `platform/tests/test_*.py` | 按文件或测试名运行 |
| 进程退出、重复消息、依赖故障、租约丢失 | `platform/tests/faults/` | `pytest tests/faults` |
| Redis、PostgreSQL、S3、Docker/OpenSandbox、ClamAV、OIDC 等真实适配器 | `platform/tests/contracts/` | 见[契约测试](../../platform/tests/contracts/README.md) |
| 确定性 Office 结构与公式检查 | `platform/tests/golden/` | `python tests/golden/run.py --self-test` |
| 文档输入的真实解析、上传、稳定性验收 | `platform/tests/acceptance/document_input/` | 见[文档输入验收](../../platform/tests/acceptance/document_input/README.md) |
| 大表查询、并发、长链等负载探针 | `platform/tests/load/` | 独立脚本或专用环境 |
| 共享测试替身与样本 | `platform/tests/fixtures/`、`platform/tests/*_fixtures.py`、`platform/tests/fakes.py` | 测试直接导入 |
| 浏览器端到端 | `platform/e2e/` | `AILAB_E2E=1 pytest e2e` |
| 真实模型、真实沙箱的场景回归 | `platform/scenarios/` | `AILAB_SCENARIOS=1 pytest scenarios` |
| GenUI 真模型评测 | `platform/evals/genui/` | 见[评测说明](../../platform/evals/genui/README.md) |
| 宿主机部署脚本验收 | `tests/` | 属于部署链路，见[部署](deploy.md) |

`platform/pyproject.toml::tool.pytest.ini_options` 把默认测试路径限制为 `tests`，并启用严格配置与 marker；`e2e`、`scenarios` 和专项脚本都要显式指定路径。

## 关键流程

### 本地修改后的验证
1. 进入 `platform`，先跑 `ruff check` 与 `ruff format --check`。
2. 只跑改动模块对应的测试；测试名要能证明新行为，不能只跑相邻文件。
3. 涉及 Office 读取时，先在 `vendor/office-suite/runtime` 执行 `npm ci --ignore-scripts && npm run build`。缺少 `runner.mjs` 是环境错误，`platform/tests/conftest.py::office_reader_built` 会让相关测试直接失败。
4. 涉及 Portal 时，在 `platform/portal` 跑 `npm test` 与 `npm run build`；涉及浏览器行为时再跑 E2E。
5. 完整 Python 套件由 CI 执行。只有复现 CI 失败、或代码暂时不能推送时，才在本地跑全套。

```bash
cd platform
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$(brew --prefix findutils)/libexec/gnubin:$PATH"  # macOS
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest tests/test_<模块>.py -q
```

复现双后端全套时，Redis 与 PostgreSQL 必须是可清空的一次性实例：

```bash
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/15 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_test \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations \
.venv/bin/pytest tests -q -n auto --dist worksteal
```

macOS 上必须把 GNU `coreutils` 和 `findutils` 放进整棵 pytest 进程树的 `PATH`；只给单条命令加前缀会让测试里的 shell 命令继续使用 BSD 工具。

### 测试隔离
`platform/tests/conftest.py` 在导入被测应用前构造独立设置：不读取开发者 `.env`，数据目录与对象存储落在该 worker 的临时目录，默认使用内存队列、mock 沙箱和 `tests/models.test.toml`。若设置 `AILAB_TEST_DATABASE_URL`，conftest 会先清空它的 `public` schema，因此它只能指向一次性数据库。

pytest-xdist 下，`platform/tests/conftest.py::_worker_postgres` 为每个 worker 创建 `<库名>_gwN`，`platform/tests/conftest.py::_worker_redis` 从给定 Redis db 向下分配；Redis db 不够时会直接报错。PostgreSQL 用户需要 `CREATEDB`，Redis URL 的 db 编号下方要留够每个 worker 一个库。不要在测试模块里缓存导入时的数据库 URL。

每个 worker 使用自己的临时目录和 SQLite 文件；`platform/tests/conftest.py::pytest_sessionfinish` 成功时删除临时状态，失败时保留并打印路径供检查。共享固定端口、固定文件路径或整库计数的测试会在并行下互相干扰，应改为临时目录、随机端口或只统计本测试创建的行。

E2E 由 `platform/e2e/conftest.py::stack` 启动四个 Python 服务、Redis 和 Portal 生产构建；默认 SQLite 与 mock 沙箱，真实后端必须显式选择且可丢弃。Redis 的 E2E 队列、租约与锁键在会话结束时清理。

### CI 验收
CI 的入口是 `.github/workflows/ci.yml`；PR 按完整 diff 选择套件，`main` push 与手动运行总是全套。范围选择器与门禁细节见 [CI](../../.github/CI.md)。

| 套件 | 内容 | 上限 |
|---|---|---|
| `changes` | 选择器单测、完整 diff 范围选择 | 3 分钟 |
| `quality` | 依赖锁、Ruff、生产依赖导入、skills、黄金样例 | 8 分钟 |
| `python` | SQLite 覆盖率和 PostgreSQL 全套；真实 Redis/PG 必验组 | 15 分钟 |
| `storage-contracts` | 真实 MinIO S3 契约 | 15 分钟 |
| `shell` | actionlint、ShellCheck、Kustomize | 5 分钟 |
| `crawler` | 爬虫组件单元测试 | 8 分钟 |
| `e2e` | Portal 单测、生产构建、浏览器用例，三片 | 20 分钟 |
| `CI` | 汇总门禁 | 2 分钟 |

稳定汇总检查名是 `CI`：它要求选中的 job 成功，未选中的 job 必须跳过。最终验收记录是**待合并 commit 上的 CI 结果**，不是本机通过数、历史绿色或单个组件结果。CI 时间上限也是验收的一部分；套件变慢要修夹具或测试代价，不能靠调高上限。

`platform/tests/conftest.py::no_sql_on_serving_loop` 会捕获在 API 事件循环上执行的阻塞 SQL；这类失败表示实现问题，不是测试抖动。

### 真模型场景
以下改动必须在真实模型、真实沙箱和当前技能上跑 `platform/scenarios/`：提示词、工具目录或工具结果、技能、请求构造/压缩、沙箱镜像，以及会影响 Work 交付链路的改动。部署前后各跑一次；它经公开 `/v1` API 驱动一个已运行的栈，实际调用会计费，只能使用获准的测试账号和额度。

```bash
cd platform
AILAB_SCENARIOS=1 \
AILAB_SCENARIO_API=http://127.0.0.1:<api-port> \
AILAB_SCENARIO_TOKEN=<bearer> \
AILAB_SCENARIO_REPORT=/tmp/<owned>/scenarios/report.jsonl \
.venv/bin/pytest scenarios -v -p no:cacheprovider
```

`AILAB_SCENARIO_MODEL` 不填时使用栈的默认模型；`AILAB_SCENARIO_MIN_CACHE` 默认 `0.85`，检查后续轮的缓存命中率；`AILAB_SCENARIO_REPLIES=1` 会把回答写入报告，便于比较质量。报告必须放在仓库外，不能提交原始回答。

场景除了业务结果，还检查 Run 成功、同一会话内没有上下文边界、后续轮达到缓存下限、未要求时不启动子任务。单次样本波动大，比较版本时要重复运行并看工具路线、失败原因和每轮 token，不能只看一次是否成功。

token 比较以账本为准。报告只含公开 API 返回的轮级 `prompt_tokens`、`cached_tokens`、`completion_tokens` 和调用数；首请求输入、被静默调用计入的用量以及加权费用只在 `model_usage` 账本里。`platform/scenarios/token_report.py::ledger` 以只读方式读取账本，`platform/scenarios/token_report.py::compare` 按场景和轮次取中位数并汇总：

```bash
.venv/bin/python -m scenarios.token_report runs /tmp/before/*.jsonl /tmp/after/*.jsonl > /tmp/run-ids.txt
.venv/bin/python -m scenarios.token_report ledger "$AILAB_DATABASE_URL" < /tmp/run-ids.txt > /tmp/usage.json
.venv/bin/python -m scenarios.token_report compare before=/tmp/before/*.jsonl after=/tmp/after/*.jsonl --usage /tmp/usage.json
```

账本在 API 容器内时，用 `docker compose exec -T api ...` 执行同一个 `token_report.py`。`interrupted` 轮次由额度暂停造成，`token_report.py` 会排除它们。没有账本数据时可以比较公开 API 的提示词/缓存/输出 token，但不能声称已经比较首请求或加权成本。

## 不变量

- `platform/tests`、`platform/e2e` 与 `platform/scenarios` 的测试不读写开发者数据库、`.env`、真实模型密钥或生产对象存储；真实后端只能是可丢弃实例。钉住它的测试：`platform/tests/conftest.py::_worker_postgres`、`platform/tests/contracts/conftest.py::engine`。
- 并行测试只能依赖自己的 worker 状态；全局清扫、固定端口、整库计数或模块级 URL 缓存都会破坏隔离。
- 真实后端契约在缺少 URL 时干净跳过；CI 中被选中的契约组不允许用 skip 冒充通过。钉住它的测试：`.github/tests/test_verify_junit.py`。
- 测试不通过增加真实等待时间掩盖竞态。租约、心跳和重试用虚拟时钟或受控释放；绝对墙钟延迟不作为共享 runner 上的断言。
- 真模型场景的终态成功不等于交付质量；必须检查实际工具调用、失败调用、回答内容和下载的交付件。
- 原始日志、回答、采样和 trace 留在仓库外；仓库只保留能复现问题的最小 fixture 和记录引用的截图。

## 怎么扩展

### 给新行为加一个会失败的测试
1. 先写能观察用户契约的测试，确认修改前失败、失败原因正是目标缺陷。
2. 放在离行为最近的目录：业务集成放 `platform/tests/test_<模块>.py`，真实适配器放 `platform/tests/contracts/`，进程/依赖故障放 `platform/tests/faults/`。
3. 测试改动完成后跑该文件；全套留给 CI，除非需要复现 CI 失败。

### 加一个故障测试
1. 在 `platform/tests/faults/` 模拟真实故障：重复投递、进程退出、依赖不可用、租约丢失或恢复后重放。
2. 使用受控时钟、明确释放信号或临时数据库，不要用 `sleep` 维持前置状态。
3. 断言恢复后的最终状态、没有重复副作用和清理完成；必要时用变异验证去掉保护后测试确实失败。

### 加一个真后端契约测试
1. 在 `platform/tests/contracts/` 用环境变量选择真实后端；未配置时 `pytest.skip`，不能静默回退到 mock。
2. 使用唯一临时 schema、bucket 或资源名，并在 `finally` 清理；不要接触共享开发库或生产服务。
3. 在 `.github/workflows/ci.yml` 中把必须执行的组加进 `verify_junit.py --require`，否则该组可以被跳过而不被发现。具体变量见[契约测试](../../platform/tests/contracts/README.md)。

### 加一个 E2E 用例
1. 在 `platform/e2e/test_<主题>.py` 标记 `pytest.mark.e2e`，使用 `stack`、`logged_in`、`page` 等 fixture，不从浏览器外部伪造已登录状态。
2. 断言用户能看到的结果和实际文件/数据库状态；等待成品出现，不用固定 sleep。
3. 本地用 `AILAB_E2E=1 .venv/bin/pytest e2e/test_<主题>.py -q` 验证。新增浏览器需要的 Portal 顶层文件或依赖时，同步检查 `platform/e2e/conftest.py::_build_portal` 的复制清单。

### 加一个真模型场景
1. 在 `platform/scenarios/test_<主题>.py` 用 `Client` 和 `Turn` 发送代表性任务；输入文件放 `platform/scenarios/inputs.py` 或仓库外的合成语料。
2. 用 `contracts()` 检查每轮通用契约，再断言用户可观察的结果；任务结束后删除自己创建的会话和文件。
3. 在真实栈上运行该场景，把 `AILAB_SCENARIO_REPORT` 指向仓库外；涉及 token 变化时按上面的账本流程做前后比较。

### 更新 GenUI 评测
1. 在 `platform/evals/genui/cases.py` 增删场景，保持固定中文输入和可判定输出。
2. 跑 `platform/evals/genui/run.py` 生成回答，再跑 `check.ts` 与 `score.py`；原始回答与检查结果留在 `/tmp` 或调用者指定目录。
3. 同步维护人工判定文件；缺失判定的处理、离线回归命令和字段见[评测说明](../../platform/evals/genui/README.md)。

### CI 因新测试变慢
先测是哪一个夹具或等待变慢，再修测试代价：缩小数据、复用不可变构建、缩短测试中的真实等待、避免 autouse fixture 扫描整表。不要先调高 CI 时限，也不要用 skip 删除覆盖。

## 配置

| 变量 | 作用 | 默认 |
|---|---|---|
| `AILAB_TEST_REDIS_URL` | 一次性 Redis；xdist 向下分配 db | 未设置时相关契约跳过 |
| `AILAB_TEST_DATABASE_URL` | 全套 SQLite 替换为一次性 PostgreSQL，导入时清空 schema | 临时 SQLite |
| `AILAB_TEST_POSTGRES_URL` | PG 迁移/契约库 | 未设置时相关契约跳过 |
| `AILAB_TEST_S3_URL`、`AILAB_TEST_S3_ACCESS_KEY`、`AILAB_TEST_S3_SECRET_KEY` | 真实 S3 契约 | 未设置时跳过 |
| `AILAB_E2E` | 启用浏览器 E2E | 关闭 |
| `AILAB_E2E_SHARD` | `k/n` 按用例 id 稳定分片 | 不分片 |
| `AILAB_E2E_SANDBOX_PROVIDER` | `mock`、`docker` 或 `opensandbox` | `mock` |
| `AILAB_E2E_SANDBOX_IMAGE` | 真实 E2E 沙箱镜像 | 无，真实后端必填 |
| `AILAB_SCENARIOS` | 启用真模型场景；同时要求 API 与 token | 关闭 |
| `AILAB_SCENARIO_API`、`AILAB_SCENARIO_TOKEN` | 已运行栈的 API 与测试账号 bearer | 无 |
| `AILAB_SCENARIO_MODEL` | 场景使用的模型 | 栈默认模型 |
| `AILAB_SCENARIO_MIN_CACHE` | 后续轮最低缓存命中率 | `0.85` |
| `AILAB_SCENARIO_REPORT` | JSONL 报告路径 | pytest 临时目录 |
| `AILAB_PERF` | 启用 Portal 性能用例 | 关闭 |

其余配置以 `platform/ailab_platform/config.py` 为准。

## 相关决策

- [ADR 0030 模型请求前缀稳定](../architecture/adr/0030-prefix-stable-model-requests.md)：决定多轮请求必须逐次追加，测试记录完整请求并检查缓存命中。

## 已知的坑

见 [HANDOFF · 测试](../../HANDOFF.MD#测试)。
