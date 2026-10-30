# GenUI 组件目录 v2 与按需披露（2026-10-30）

基线：`feat/frontend-genui`，HEAD/base `origin/main@97793e6`，全部未提交。决定见 [ADR 0029](../../architecture/adr/0029-genui-registry-v2.md)，设计见 [registry v2](../../architecture/design/2026-10-30-genui-registry-v2.md)。分工：前端、提示词和全局审核由 Claude 完成；后端、评测和部署由 Codex 完成，Claude 审核。

## 做了什么

- **组件目录**：从 21 个扩到 57 个。派生数字由组件计算：Highlights、Metric `previous`、Ranking、Compare `better`、Waterfall、Funnel、ShareBar/Pie、Dumbbell。高级图表和图示单独懒加载，分别为 7.5 KB 和 4.2 KB gz。
- **按需披露**：
  - 系统提示只放索引，指南和组件文档由 `ui_components` 按需返回。指南每个 Run 只发一次，上下文压缩后重发。
  - 首次调用的实测输入：Chat 系统提示由 5,604 降到 1,448 token，Work 由 6,307 降到 2,150 token（离线估计）。线上首个模型调用的输入：Chat 约 2.3k，Work 约 3.1k token，含用户消息与工具 schema。
  - 第五轮评测后，决定回答对错的规则移回了索引，见下文。
- **评测检查器**：`evals/genui/check.ts` 改用 `shapeOk`，并接受任意组件作根。旧的 v1 检查器 `portal/scripts/genui-eval-check.ts` 已删除。

## 第五轮评测与根因修复

结果见[评测报告·第五轮](2026-10-30-genui-real-model-eval.md#第五轮catalog-v2)和[复测](2026-10-30-genui-real-model-eval.md#第五轮复测根因修复后)。全集严格一致性为 5/23，比第四轮的 11/23 更低。根因有三：

1. **规则放错了地方。** “数字和结论由界面给出”“块外只解读”“不做未要求的统计”被移进了指南，而指南是工具返回结果，模型对它的遵守不如系统提示。这三条已移回索引，约增加 130 token。
2. **契约比渲染更严。**
   - `root = Ranking(...)` 在 Portal 中能正常显示，只有检查器把它判为失败。
   - Highlights 的 `facts` 写成 `sum` 会让整块失效。现在接受同义词，按文档名称归一。
3. **未转义引号截断文字。** 中文字符串里用 ASCII 引号引用词语，文字会被截断。`toProgram` 现在按“引号之后能否结束字符串”来修复。

复测范围是 21 个失败场景，只跑一次：同一子集的严格一致性从 1/19 升到 7/19，合法块从 30/33 升到 33/33。**P6 仍未通过。** 剩下的失败几乎都是模型在块外复述数字或比较：26 条回答里有 15 条。其中确实写错的只有 2 处，另有几处属于没有依据的推断（原因、所谓“正常水平”、预测）。

## 可视检查与修复

用临时页面渲染全部组件，在 1280/375 宽、浅色/深色下截图（做法见 HANDOFF）。没有控制台错误，没有形状错误，也没有横向滚动。修复的问题：

- 卡片内的 KeyValue 继承了 Grid 的 `--cols`。
- Progress 标签被挤成一字一行。
- 双轴图和散点图的轴单位压在刻度上，最大的气泡被裁切。
- 直方图出现小数刻度，且缺最后一个边界刻度。
- Flow 回边穿过节点（改为正交折线绕行），向下布局时没有居中。
- Diff 先显示新行后显示旧行。
- 手机上 Grid 里的图表被挤成半宽。
- Compare 的行标签没有内边距。
- 甘特图最后一个刻度被裁切：线上截图发现，已修复。

## 部署与真模型验收

Codex 增量部署 `genui-v2-5bf1a1e460a6`：只重建后端共用镜像和 Portal。sync、build、up 均 exit 0，服务 healthy，Portal 返回 200，迁移仍在 0039。未备份，部署后未另跑测试。命令与报告只保存在本机 AILab-local 的 state 目录。

真模型验收共 9 例（Chat 6 例，Work 3 例），截图在仓库外：

- 全部渲染正常，没有形状或解析错误，也没有页面 JS 异常。
- RAG 问答没有出界面。
- 计算器与模拟都走了 HtmlArtifact，计算正确。
- Flow、Gantt、Waterfall 的数据都正确。
- 正文仍有模型推理错误：“翻倍级快充”实际是 1.49 倍；排期把重叠任务说成“严格串行”；Work 销售结论把回落月份和上涨次数写错，与图表矛盾。

## 检查

- Portal：vitest 19 个文件、96 项通过，typecheck 和 build 通过。
- Python：Ruff check/format 通过；全套测试跑过一次，唯一失败是 Codex 当时正在修改的评测 harness，单独重跑通过；`test_genui` 与 harness 测试通过；评测离线 4 项通过。
- 最后的甘特刻度修复只跑了组件测试和 typecheck，没有重跑全套测试。

## 多块穿插（用户要求放宽）

- 索引改为：回答分几部分讲时，每部分在对应文字旁放自己的界面块，只有一个要点时用一个块；块之间不共享数据和变量，需要联动的内容放在同一块。
- 指南中每个回答的块数上限由 4 改为 `MAX_BLOCKS`（8），与 Portal 和后端的渲染上限一致。
- 契约已重新生成，GenUI 前端测试 27 项、Python `test_genui` 通过。这次改动没有做付费评测。已增量部署 `genui-v2-695dbf2d51d0`：migration-before、sync、build-backend、build-portal、up、migration-after、health 均 exit 0；12 服务 running，配置健康检查全部 healthy，Portal HTTP 200，Alembic head/database 0039。只重建后端共享镜像与 Portal，无备份及部署后测试。
- 本轮发布前 Ruff check/format（464 文件）、Portal `npm test`（96 项）通过；代码基线为 `97793e6` 上的本轮完整未提交树，完整套件由 PR CI 执行。

## 未做与风险

- **P6 未达到 100%。** 用户已把门槛改为“没有错误陈述”。随后两轮提示词迭代（v6 与 v7），采用 v6：在两种模式的基础提示中加入一条通用的数据陈述规则，严格项 20/23。迭代已停止，见 [迭代 v6/v7](2026-10-30-genui-real-model-eval.md#迭代-v7)。
- v1 历史回答不再按 v1 渲染（用户确认历史回答不需要保留）：位置参数错位的组件会被单独丢弃。
- 没有做打印和分享页的新组件专项检查，没有做 4×CPU 性能复测。
