# Office P2 制作与检查（2026-11-02）

分支 `feat/office-suite`，进入基线 `d8ed0f9`，base `origin/main@bf0af4f`；本地无 fetch。用户授权核查并清理取消运行留下的 P2 改动，不 push、PR、部署。P3 在另一 worktree；本轮没有改 doc refs、view state 或 document_view。

## 实现与产品合同

- `0012bca`：取消8192打包意图。固定 `load_skill(name, resource?)` 一次读取完整导航或白名单文件；manifest按文件记录path/hash/estimated_tokens/modes/deps，无token分片/cursor/大小硬限/截断。保留配对同hash正文去重、压缩后重读和ModelPolicy同组累计预算；放不下明确 cannot fit, compact first。P1历史分页说明由本决定取代。
- `c957371`：保留并完成取消运行中的离线镜像、runtime补丁重基与路径迁移；删除旧reader目录，原始源码仍在vendor/upstream，生成reader由显式补丁和runtime-adapter重建。移除不用于reader执行的生成源码合同文档；保留全部原始来源。依赖按镜像digest、Debian snapshot、Python版本/hash及npm lock固定，分系统、Python、Node、技能层；运行时不联网安装，无新增常驻服务。许可记录见[镜像依赖](../../../images/sbx-office/DEPENDENCIES.md)。
- `d83499f`：转换默认写 `/workspace/outputs`，离线XeLaTeX替代会联网补包的平台专用引擎，禁止shell escape；缺依赖报错不再要求运行时安装。XLSX原地命令拒绝inputs路径；新版本保持原件不动，最后通过deliver_file交付。原始脚本不直接修改，所有变化由patches重放。
- `70bf6ba`：固定Work工具 `review_artifact(path, pages?)`，Broker复用现有租约/渲染槽，在受限沙箱提供结构证据、file hash、页数/覆盖及最多4页图。继承20MiB输入、ZIP展开128MiB/2048条/200倍、45秒执行/60秒总时限、1280px、512KiB单图/1200KiB总图、checkpoint和沙箱资源限额。工具只返回pending_model_judgment，主模型逐页判内容/公式/图表/布局；tools-only只收结构文字及明确no visual review，不静默换模型、不新增付费侧调用。exit0/WARN/文件存在不算完成。
- `271915c`：复查发现评审角色含Work shell设置却可在Chat读；按整文件模式白名单改为Work，增加回归。评审指令补充逐页pass/fail/Unverified与具体证据，不虚报未检查页。

取消运行的Dockerfile/CI/deploy脚本路径改动仅保留runtime重基所需部分；未运行deploy.sh、CI或外部发布。已有API/Worker parser revision同步只针对原生读取缓存版本，不涉及P3新合同。

## 验证

产品基线 `271915c`，完整套件测试基线 `7546c48`，失败修复/定点验证基线为 `e978848` 的相同代码；最终文档提交不改变代码。原始日志、JUnit、镜像构建输出临时写在仓库外 `/private/tmp/office-p2-resume`，本记录归纳必要证据后清理，不入库。

- Ruff：在platform目录执行 `.venv/bin/ruff check . ../vendor/office-suite/{build,inventory}.py` 与同范围format --check，通过。第一次从仓库根显式config运行造成isort模块分类差异；改回项目cwd，没有批量改无关imports。
- 受影响专项：skill_modules、office_suite_resources、prefix_cache、artifact_review、workspace_view、base_office_skills、library_render，**132 passed**（46.82秒）。首次收集缺少仅沙箱使用的defusedxml，补入dev测试依赖后才执行用例。
- 追加runtime确定性重建测试1项、unknown package测试1项通过；capabilities清单测试曾因说明文字修改后未重建JSON失败，已重新生成，最终由完整套件复核。后续模式白名单与原文补丁变更也由最终套件覆盖。
- reader原始/适配Node测试：`npm test --prefix vendor/office-suite/runtime` **15 passed**；另执行原始`src/domain/text.test.ts` **4 passed**。原始217文件hash闭包与生成漂移由Python资源回归验证。
- 真实ARM64镜像：`AILAB_TEST_OFFICE_IMAGE=ee58c7eed3b5 .venv/bin/pytest tests/contracts/test_office_production.py -s -q` 首次实际执行 **4 passed / 1 failed**（54.43秒）。四格式各自创建、编辑原件副本、转换、原生reader重读、真实review helper结构/页图均通过；Excel验证活公式与重算值，PPT验证原生图表仍可编辑，DOCX调用原始编辑库，PDF调用原始合并CLI；全部原件hash不变。
- 唯一重路线失败是HTML PDF提取的兼容连字使字面文本断言失败，生成文件有效；同断言复跑仍失败。测试改用NFKC文本归一化后**仅复跑该失败项，1 passed**（4.70秒），断网HTML和XeLaTeX均通过。修复见 `7546c48`，未修改产品或放松内容断言。较早在tag完成前误启动的调用是5个setup errors，未执行制作，不计成功用例。
- 最终模式元数据镜像 `52efc1239e95` 与上述测试镜像的**42个脚本/reader bundle/依赖lock文件内容指纹完全一致**（`f57cc893fc2e4f08…`）；最终镜像index SHA256为`ef8e5b817c17d490…`，与当前源树一致。未重复已成功的制作测试；最终整文件/模式变化由完整Python套件验证。
- 完整Python **仅执行一次**：在 `7546c48` 执行 `PATH=<GNU coreutils/findutils> .venv/bin/pytest tests -q --junitxml=/private/tmp/office-p2-resume/full.xml`，**2012 passed / 167 skipped / 20 failed**，350.34秒。18项因最后一次重生成runtime删除了本地runner及node_modules（此前构建已失效），不是镜像依赖缺失。其余2项是能力清单仍断言已安装依赖缺失，以及沙箱helper在服务模块顶层导入defusedxml；分别修正为检查闭包已提供、把沙箱专用导入放在执行函数内。parser升级测试的期待版本同步新revision。
- 最后重生成之后执行 `npm ci --ignore-scripts --no-audit --no-fund --prefix vendor/office-suite/runtime && npm run build --prefix vendor/office-suite/runtime`；从JUnit精确提取上述20个失败node，**只复跑20项，20 passed**（14.93秒），修复提交 `e978848`。Ruff check/format再次通过（522文件）。没有重跑完整套件，也不将定点结果写成“最终全套首次全绿”。没有出现需放宽阈值的内存flake。
- 167项skip为未配置的可选真实后端/环境及本轮新增5个默认未设置镜像变量的合同；新增5项已在前述真实镜像专项单独覆盖。PG/Redis等其他skip不视为验收。已知原始docstring无效转义和Starlette弃用警告保留。

## 镜像与资源成本

在仓库根执行 `/usr/bin/time -p docker build -t c3-office-suite-p2:local -f images/sbx-office/Dockerfile .` 构建镜像，使用交接中现有Docker daemon，仅创建 `c3-office-p2-*` 一次性测试容器，不改用户服务。

首次构建 **510.05秒后失败**：Python层解析PyPI域名失败，日志中的No matching distribution是DNS后果，不是版本已证伪。复用已完成系统层，只重试失败层；构建成功后仍需因Chat模式过滤回归刷新技能元数据层。构建次数/耗时均如实计入，不称“首次全绿”。

基线镜像为现有 `ailab-local-office:release-41dc893`，Docker inspect Size **463,330,502 bytes**，image id `9c1901fb5d54`；不是本轮部署记录。最终镜像 `52efc1239e95` Docker inspect Size **1,256,182,262 bytes**，相对基线增加 **792,851,760 bytes**（约756.12MiB）。依赖层复用后的成功构建 **435.46秒**；模式隔离缺陷修正后，仅刷新技能层的增量构建 **197.70秒**。本轮共3次构建调用：1失败、2成功，总耗时**1143.21秒**；没有再做第二次冷构建。这里记录Docker报告的Size，不冒充压缩分发或解压实际磁盘测量；无push。依赖闭包的体积/冷构建开销显著，但未提升1CPU/1.5GiB作业限额。

| 真实断网单作业 | 脚本耗时（秒） | 容器cgroup峰值（MiB） | CPU usage（秒） |
| --- | ---: | ---: | ---: |
| DOCX创建/原始库编辑/转换/重读/评审 | 13.90 | 436.61 | 3.17 |
| XLSX创建/活公式编辑重算/转换/重读/评审 | 14.50 | 312.60 | 3.41 |
| PPTX创建/保留可编辑图表/转换/重读/评审 | 13.00 | 253.03 | 2.54 |
| PDF创建/原始CLI合并/转图/重读/评审 | 1.89 | 141.46 | 1.44 |

耗时从fixture import后开始；cgroup峰值包含子进程及缓存，不是单进程RSS。均为一次小样本且当时有镜像元数据层构建，不能推导并发容量。HTML/LaTeX组合失败项修正后共4.70秒（含pytest/container开销），未单独测其RSS。工具schema静态估算新增约207 token（完整Work目录约3864），Chat不含review_artifact；没有新增常驻服务或数据库查询。

## 未测边界与下一步

- 真实模型视觉判断、内容正确率、供应商cached_tokens/prompt_tokens≥95%、独立视觉模型、跨重启评审结果复用、并发整栈资源隔离属于P5联合验收。本轮只有录制请求前缀证据，不能把渲染成功当视觉通过。tools-only仍渲染选定页后丢弃图片，仅返回结构文字；此类任务有本轮单批渲染成本，尚未增加独立视觉fallback。
- 实际五服务copy_library_file→制作→deliver_file与品牌/模板多轮交付未跑；本轮镜像合同覆盖基础四格式create/edit/convert、原件hash和实际结构/页图。复杂修订/批注/目录/分节、全量模板/图标、财务模型/数据透视、VBA保留/Windows执行、全部LaTeX宏包/中文复杂排版、HTML数学/图表/海报场景未逐项验收；能力清单仍保留planned/blocked，未批量标完工。
- Portal/browser、PostgreSQL/Redis、OpenSandbox/runsc、amd64镜像及部署未执行；无Portal代码变更。新依赖许可与所需字体/宏包的外部分发条件沿用镜像依赖记录，不能宣称所有办公规范完全兼容。
- 下一步：交主任务评审本地提交与上述未测边界，再与独立P3/P4/P5整合；本轮基础制作/检查合同与定点修复已验证，不自动发布或部署。
