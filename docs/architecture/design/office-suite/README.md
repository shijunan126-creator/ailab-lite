# 上游办公能力完整迁移计划

日期：2026-11-02。状态：**已授权实施（2026-11-02）**，分支 `feat/office-suite`；实施决策见下节，进度见 [current](../../../progress/current.md)。下文“本计划不授权……”为原 plan 阶段限制，已由实施授权取代（部署仍需另行授权）。用户要求尽量直接采用 上游 原始提示词和能力，适配 AILab Chat/Work，包括侧栏、各种压缩恢复、Prefix Cache 和上下文容量。本计划不授权安装依赖、产品修改、构建、真模型调用、PR、合并或部署。

## 实施决策（2026-11-02）

上游源码根下文记为 `$UPSTREAM`（本机路径只记录在 `vendor/office-suite/UPSTREAM.json`）。

| # | 决策 |
| --- | --- |
| D1 许可 | 四制作技能 frontmatter 写 “Proprietary. LICENSE.txt has complete terms”，而 LICENSE.txt 全文为 Apache-2.0，按完整条款 Apache-2.0 分发；office-runtime 为 MIT。原 LICENSE 文件原样保留在 vendor，NOTICE 记录来源与修改；每个生成技能包随附 Apache-2.0 LICENSE 和 NOTICE，第三方图标目录随附其 LICENSE-lucide.txt，由构建生成并纳入漂移检查；镜像新增依赖逐项登记许可。 |
| D2 命名 | 模型可见文本（技能正文、模块、工具描述/结果、提示词）不出现上游产品名、上游专用环境变量或其 citation 标签；适配补丁替换为 AILab 工具与语法，测试对生成资源树与录制请求做大小写不敏感断言。许可与来源文件不改。 |
| D3 布局 | `vendor/office-suite/upstream/` 原样存放固定提交的四插件 skills/agents 与 office-runtime；`vendor/office-suite/patches/` 有序显式补丁；`vendor/office-suite/build.py` 生成 `images/sbx-office/skills/platform.{docx,xlsx,pptx,pdf}/` 及模块索引。生成物入库，测试重建并比对漂移。旧 reader 目录 迁到同一 runtime 版本后删除，不长期并存两套 parser。 |
| D4 整文件读取 | 固定 `load_skill(name, resource?)`；不传 resource 返回完整 SKILL.md 导航和文件目录，传 resource 一次返回白名单中的完整文件。manifest 按文件记录 path/hash/estimated_tokens/modes/deps；无分片、cursor、大小硬限或截断。Chat 不见脚本执行材料。整文件放不下剩余上下文时明确返回 cannot fit, compact first；当前配对消息中已有同 hash 完整文件才省略重读。 |
| D5 可见性 | “模型已读”只由当前 messages 中配对成功、完整、同 hash 的结果推导；`loaded_skills` 仅表示安装/登记，不再用于拒绝重读。三类压缩与恢复后重算。 |
| D6 视觉验收 | 系统级 delivery protocol 未取得，不宣称迁移；AILab 本地固定 `review_artifact` 在受限沙箱渲染成品并返回结构证据与最多4页图，主模型在同一固定工具目录和追加消息中审阅。工具结果始终 pending_model_judgment，工具本身不判质量通过；tools-only 返回结构文字和明确 no visual review。独立视觉模型仍是后续联合验收边界。 |
| D7 侧栏工具 | 固定工具 `document_view`（op: open/get_view/reveal/capture），Chat/Work 同 schema；选区以消息 `doc_refs` 经 outbox 发送，API 复核。 |
| D8 分工 | 后端实现与全部测试由 Codex（gpt-6.1-sol medium）完成；Portal 侧栏与选区 UI 由 Claude 实现；Claude 负责评审与最终质量。PR 不自动部署。 |

## 目标与范围

迁移 `documents-plugin`、`spreadsheets-plugin`、`presentations-plugin`、`pdf-plugin` 四个办公插件的制作技能、viewer 技能、脚本/参考/资源、Office Runtime、交互侧栏和视觉验收流程。不是迁移 上游 全部插件、Agent 框架或账号配置。搜图、下载、OCR 等周边能力映射 AILab 服务，不能凭办公包存在就宣称外部服务可用。

**保留完整上游能力，不再把它改写成几十行简单生成说明。** 原始文件固定版本、原样留存，运行版本由显式适配补丁生成；每项功能和每处改动都可追溯。按需加载的是原文模块，不能以省 token 为由用摘要替代操作规范。完整能力安装在系统中，不等于每次请求装进全部正文。

Chat 获得读取、搜索、结构/选区引用、视觉理解和侧栏协作；保持无用户沙箱执行、无文件制作交付。Work 再获得完整创建/编辑/排版/分析/转换/检查/交付能力。二者共享文件授权、精确版本、派生缓存和侧栏。具体任务能否执行还取决于模型 tools/vision 能力、运行依赖和资源预算，不能承诺所有模型等价。

## 基线与证据

- AILab 本机工作树：`/Users/michael/Developer/ailab-lite`，`fix/source-pill-alignment@c033664c4d9d5837283193f44fbd9ce2ede306e9`，含进入任务前已有 UI/交接等未提交修改，均保留；origin 为 `https://github.com/shijunan126-creator/ailab-lite.git`。本轮未 fetch，实施前重新核对远端基线。
- 上游：`$UPSTREAM_REPO`，干净 `main@a60ba6a7e5a06742270ca605f5d2cc2766afa7e1`；完整绝对路径和用途见[源码调研](../../../reviews/2026-11-02-office-skills-research.md)。本计划源码均指该固定本地版本。
- AILab 已有 vendor 固定 `3c5b227649371507ddc4eb46ac5133afe6db1cab`。两提交办公相关目录差异集中在 runtime 状态/会话、文件访问、server 与 viewer 指导；不可直接覆盖现有 AILab patch。制作技能在这两个提交间未见差异，不把当前不足归因为“只差一次升级”。
- 本轮静态审查了请求构造、技能加载、三类压缩、附件准入、checkpoint、子任务、预览和上游 UI bridge。未执行运行时复现、测试、构建、浏览器或模型实验；下文缺口是源码路径判断，性能和效果均待实施验收。

## 模块与阅读顺序

| 文档 | 唯一维护内容 |
| --- | --- |
| [01 当前系统调查](01-current-system.md) | AILab 真实路径、已存在合同、阻碍完整迁移的缺口 |
| [02 原文与能力迁移](02-upstream-and-adaptation.md) | 上游路径、全量能力归属、原文保留、适配清单、依赖与许可 |
| [03 上下文与 Prefix Cache](03-context-and-prefix-cache.md) | 模块协议、加载/去重、预算、三类压缩、恢复与版本边界 |
| [04 侧栏与文件合同](04-viewer-and-file-contract.md) | UI 复用、工具映射、选区/版本/权限、离线和多端 |
| [05 实施与验收](05-delivery-and-acceptance.md) | 阶段、验收矩阵、性能/成本门槛、未决项和下一步 |

## 决策顺序

1. 固定上游来源并逐功能登记可复用对象，确认许可、完整依赖闭包和对 AILab 的必要适配。
2. 先建模块读取/预算/压缩恢复合同，使大技能不会绕过输入准入，也不会因已加载标记而无法重读。
3. 在固定工具目录下接读取、制作、检查与单个视觉评审，保留 AILab 计费、幂等、租约和文件交付权威。
4. 复用 viewer 实现，替换 上游 host/RPC，接 Portal 侧栏、选区引用与模型定位；按格式验收，不把只看 PDF 当完整 parity。
5. 用同模型/同输入对照、长会话/压缩矩阵与实际供应商缓存计数证明效果，并记录 CPU/RSS/磁盘/时延成本。

## 跨模块不变式

- API 是文件身份、授权、版本、引用及业务数据库的唯一权威；解析/渲染/脚本仍在受限执行环境，Portal 不直连内部服务或持密钥。
- 同一上下文段只追加消息，固定 system/tools/reasoning/tool_choice；模块、文件、侧栏、记忆变化都通过追加的消息/工具结果表达。仅在显式边界重建。
- 完整原文与原始文件保留在受控资源层；上下文只载入任务所需原文，正文与图片都计真实预算。预算不足应压缩/分段/暂停，不伪装完整读取。
- 资源安装状态、模型实际可见说明、执行进度、UI 查看状态是四件事；摘要和永久布尔标记不能代替任何一种权威记录。
- 接入完成须覆盖原件编辑、精确引用、侧栏互操作、视觉检查、压缩恢复和失败分支；不以测试数量或某个格式能打开声称整体完成。

## 与既有设计的关系

本计划是四办公插件完整能力迁移的维护入口；既有[09 上游复用](../file-context-ocr/09-upstream-reuse.md)保留历史读取接入依据并链接此处。[Prefix Cache 设计](../2026-10-31-prefix-cache.md)和[ADR 0030](../../adr/0030-prefix-stable-model-requests.md)继续约束请求构造；旧能力发现文档中的动态增删 schema 已被后续固定目录合同取代，不能据旧 plan 回退。实施时若修改已采纳合同，须同步 ADR、测试和状态矩阵。

构建合同整合修订（2026-11-02）：整文件读取不变；为匹配loader，构建拒绝 SKILL.md >100,000 bytes，其他可读取资源 >256 KiB 或 >2000行。运行时不以截断规避，取代本页早期“无大小硬限”的描述。
