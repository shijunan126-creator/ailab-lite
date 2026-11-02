# 办公资源 P0 与确定性构建（2026-11-02）

范围：仅 P0＋资源构建；未开始 P1。分支 `feat/office-suite`，进入基线 `d92b81a270ffdf1a4e93db922386d375283e79b9`，base `origin/main@bf0af4f`；保留并发进入的侧栏接口设计提交 `ae996a0`。本轮来源提交 `15606c4`，资源提交 `3c72160`，兼容修复 `a6c440d`、围栏修复 `13d630a`；最终产品/测试代码基线 `13d630a`；未 push、PR、部署。

## 产物

- `vendor/office-suite/upstream/` 原样保存217个固定提交文件（含四插件技能、角色、manifest及runtime源码/UI/脚本/fixture/测试/许可证），`UPSTREAM.json`为完整SHA-256闭包；NOTICE记录Apache-2.0/MIT与本地补丁义务。
- 三份有序精确补丁及`build.py`生成四基础技能：113个完整原文模块，按UTF-8字节范围/源行段/hash/token/modes/deps建索引，保留模板、路由、场景、质量检查、viewer与视觉评审说明。导航515–775估算token；旧JSON规格生成器/检查器/图标由compatibility输入重建，公共路径不变。生成树无上游产品名、账户/模型默认值；随包携带 Apache-2.0 LICENSE、NOTICE 及图标目录许可。
- `capabilities.json`为336项静态机器契约：逐源文件、CLI命令/文档API、manifest命令、四格式各9项viewer特性及两个跨功能边界。列出Python/npm/二进制与动态调用表达式、镜像缺口、目标模式/位置和验收ID；不能把静态扫描当运行闭包已验收。
- 既有skill index已递归包含完整目录，无需改变算法；新回归证明模块正文、深层脚本和模块元数据任一漂移都会改变hash并使Agent证明失败。Dockerfile已有整树COPY，无需改镜像或安装依赖。未修改registry/loader/loop、固定工具schema、模型请求构造或Portal。

## 验证

在上述代码基线使用本worktree真实路径的Python3.12 venv，按仓库约定前置GNU coreutils/findutils。第一轮最终专项（`3c72160`）122 passed；覆盖资源漂移、完整保留/模式/围栏边界、无品牌、递归证明、基础技能、旧检查器、技能治理/设置/策略/个人/平台技能及prefix。

- `cd platform && .venv/bin/ruff check . && .venv/bin/ruff format --check .`通过；vendor构建/清单工具另用同一配置检查、格式检查通过。
- 完整Python套件仅执行一次（`3c72160`）：1978 passed / 162 skipped / 19 failed，347.57s。15项因fresh worktree缺既有reader构建产物；3项揭示导航遗漏`office-read`、生成器绝对路径及平台等待说明；1项为内存阈值偶发失败（7.72MB超过6MiB）。补齐既有reader测试前置（该目录`npm ci --ignore-scripts && npm run build`），保留其固定依赖，修复导航后，19失败项首轮18通过、1等待说明断言失败；最后修复后该项及资源/基础技能/提示词共27项通过。内存测试复跑通过，未放宽阈值或修改生命周期产品逻辑。全部原失败项均已通过；未重复整套，不将分项复测写成整套首次全绿。
- 最终导航兼容修复基线`a6c440d`的prefix专项47 passed，平台Ruff及skill index校验通过。
- 上游PDF主文的一条多余围栏会把正常正文误识别为4654-token代码块；`003-markdown-boundary`只删除该标记，并收紧代码围栏结束条件，操作正文完整保留。最终基线`13d630a`的资源回归10 passed，包含漂移、完整保留、围栏、模式、无产品名与证明闭包；平台Ruff/格式及skill index检查通过。
- 旧接口与两个不可拆块的例外均由测试覆盖；上游docstring无效转义产生SyntaxWarning，当前FastAPI/Starlette依赖另有弃用提示（完整套件另有既有迁移反射警告），未改写原始来源来消除警告。
- 资源构建基线`3c72160`实测一次约0.98s wall、0.83s user CPU、最大RSS约44.4MiB；四生成包实际文件合计2,635,769字节。这里只测构建期成本；新增资源增加磁盘与递归hash I/O，租约校验开销未实测。无新增常驻服务/后台任务/运行依赖；镜像及重路线CPU/RSS/时延待P2/P5。

## 未完成边界与下一步

能力状态：7 implemented（许可来源留存与分片构建边界），54 planned:P1，182 planned:P2，36 planned:P3，36 planned:P4，21 blocked。这里的implemented不是制作/侧栏功能已验收。

blocked包括禁用宿主/网络安装的环境路线（含环境说明/字体清单）、`env.fix`、Linux缺Windows Excel/COM执行、未取得的系统级delivery protocol。镜像缺少ReportLab、PyMuPDF、pikepdf、pdfplumber、defusedxml、相关npm制作/渲染依赖、Chromium、Pandoc、Tectonic等；完整列表与动态表达式仅维护在能力合同。P2需许可/离线资源闭包和实际制作验收，不能让任务运行时自动安装；视觉门禁须明确本地实现。

2048估算token是打包目标，两个原文完整围栏分别为2219、2089估算token（Word decorations/design-system）。按已确认的D4保留完整围栏，索引仅标注`oversize: true`作为信息；单段落/围栏硬限制为4096，超过则构建失败。两个现有块均满足边界，不再阻塞P0；运行时真实窗口预算仍留P1，不在本轮实现。

未运行新runtime构建/新镜像、PostgreSQL/Redis外部合同、浏览器、Portal tests/build或真模型验收；本轮未改这些运行/前端/请求路径。下一步交主任务评审本地提交，再由后续P1任务实现受控resource/cursor读取、实际可见性及窗口准入；资源存在不授权继续P1–P5。

## P0 小幅跟进（2026-11-02）

进入基线 `b5c0933`；仅修改分片构建规则与随包许可，不开始P1。构建清单明确2048目标/4096硬限制；能力合同将分片边界从blocked改为implemented，保留336项总数。四包LICENSE来自固定原始来源，NOTICE来自vendor，138个图标保留同目录LICENSE；递归hash同步更新，许可文件不作为模块索引项。

验证基线为 `b5c0933` 加本节对应工作树改动：在 `platform/` 执行 `.venv/bin/ruff check --config pyproject.toml ../vendor/office-suite/build.py ../vendor/office-suite/inventory.py tests/test_office_suite_resources.py` 及同范围 `ruff format --check`，3个Python文件通过。资源专项21项（含逐字节重建漂移、许可闭包与2048/2049/4096/4097边界）和 `tests/test_prefix_cache.py` 47项合计首轮67 passed / 1 failed；唯一失败是测试围栏样例1462估算token未达到其“独立超目标块”断言，改为2887后仅复跑该项，1 passed。全部68个唯一用例已通过，未重复整组；不是整组首次全绿。漂移用例已包含在资源专项内，不重复运行。保留既有docstring转义及Starlette弃用警告。未运行全套、Portal、外部服务、浏览器或真模型；未改模型请求路径，无新增依赖、常驻服务或运行计算，仅增加随包文本及递归hash输入。下一步交主任务评审本地提交；不push、PR或部署，P1保持未开始。
