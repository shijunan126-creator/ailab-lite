# GenUI P6 真实模型评测

固定中文集在 `cases.py`：37 个场景、43 次回答（11 个数据展示、6 个控件、4 个 HTML、10 个不应出 UI、6 个两轮编辑）。数据全部内嵌在用户消息。`judgments.json` 仅存旧提示词首轮人工判定与理由；`judgments-v2.json` 与 `judgments-v2-retry.json` 分别存新提示词全集及失败子集判定，不存原回答。第三轮根因修复后的全集/唯一失败子集判定为 `judgments-v3.json` / `judgments-v3-retry.json`。第四轮全集为 `judgments-v4.json`；第五轮 catalog v2 + `ui_components` 全集为 `judgments-v5.json`，均按当次指令不复测。v5 逐条审核43条回答，HTML/普通问答只存适用的诊断项，不扩大23项数据分母。

从仓库根运行；真实调用有费用，需当次授权。默认读取当前 `platform/models.toml` 的 `deepseek-v4.1-flash`，模型别名不替换成本机部署别名。

```bash
platform/.venv/bin/python platform/evals/genui/run.py --label v5
OPENUI_TELEMETRY_DISABLED=1 node platform/evals/genui/check.ts /tmp/ailab-genui-eval/v5.jsonl > /tmp/ailab-genui-eval/v5-check.jsonl
platform/.venv/bin/python platform/evals/genui/score.py /tmp/ailab-genui-eval/v5.jsonl /tmp/ailab-genui-eval/v5-check.jsonl --judgments platform/evals/genui/judgments-v5.json
```

密钥只读进进程环境：优先 `AILAB_KEY_DEEPSEEK`，否则使用个人 AILab-local 已有 `config/deepseek.env`；不创建密钥文件，不回显异常正文。找不到密钥时停止真实调用。mock 链路检查：`--model ailab-demo --label mock --cases calc-subscription,edit-pie`，只对 mock 附加仓库原有的 `[[genui-interactive]]` 标记，不能用它度量生成质量。

调用直接复用 `ModelTransport.chat_stream`、`ModelCall`：temperature 0.2、SSE usage、上游模型映射、connect 5s/read 90s/total 240s、最多 3 并发、每次 32,768 输出 tokens（含 reasoning）、不自动重试。v5 系统提示只带 catalog v2 索引，提供 `ui_components`，复用 Agent 的本地工具实现；每条回答独立状态，最多3轮工具调用后再生成最终回答。首次工具结果带语言指南，后续按需读组件文档；记录 `tool_call_count`、`tool_round_count`、请求的 `component_names`，usage 累加所有往返。这覆盖该工具循环，不是完整 Agent/服务链路。第二轮将第一轮正文经过 `for_history(keep_ui=True, limit=16000)` 原样作为 assistant 历史，无 reasoning 字段，与最近一轮历史合同一致。

检查器 `check.ts` 复用 Portal 拆分器、当前目录、OpenUI parser 与 evaluator（通过现有 Portal node_modules，不安装依赖）。签名会自动识别 `decimals`，但 parser 不强制数值范围和字符串最大长度，因此额外核对 decimals 为 0–4 整数、当前目录的块/HTML/fallback 上限及每块 ≤60 条语句。块大小按 Unicode 字符计，HTML/fallback 按 schema 的 JS 字符串长度计。`score.py` 只聚合检查结果，不另存上限。块合法要求：已闭合、Stack root、无解析/prop 错误、无未解析引用/未知组件、无默认求值错误，HTML document/fallback 非空。求值后的 Table/LineChart/BarChart/PieChart 直接复用 Portal 的 `tableRows`/`chartData`/`pieData`；默认值及独立控件探针任一形状错误令块 `displaysCorrectly=false`，自动计入该 case 的严格一致性失败与编辑失败，另计 `malformed_evaluated_blocks`。闭合块合法率仍为原语法口径，不能冒充显示正确率。HTTP 字面量、fetch、localStorage 独立计为 HTML 策略告警，不混入语法合法率；SVG 命名空间声明/createElementNS 中的标准 URI 仍保留字面量告警，但不计联网失败，资源 URL 仍拦截。控件探针从各自默认值独立改变一个状态，检查结果随表达式变化；Tabs/Input 计为内置控件，但Tabs切换由静态人工审核，不伪称已有状态探针。不执行生成的 HTML/JS；人工发现的 `html_success=false` 也进入失败集。

`score.py` 的数字字面量检查只生成核对候选，不能证明计算正确。P6 数据样本的 `strict_consistent` 要求块合法、显示正确、整份回答没有错误陈述；编辑还须确实完成请求，控件/HTML 仍须满足原有选择和功能门槛。错误包括正文、UI、Callout/hint、HTML fallback 中与给定数据矛盾的值/数量/月次/排名/方向/倍数或夸大差距、无依据却当事实的原因/依赖/正常水平/预测/统计检验，以及错误 UI 表达式；明确标作“可能”“推测”等的猜测不算错。

每份人工判定保存 `wrong_statements`（短引文列表，无错为 `[]`）、`restated_outside`（块外复述数据结果/排名/比较）、`strict_consistent`；错误逐条理由和分类见 `rejudgment_note` / `wrong_statement_kinds`。正确手写数字与块外复述不再导致失败，`numeric_correct`、`expression_provenance`、`conclusion_compliant` 仅作诊断，原轮诊断及 note 保留，`data_consistent` 为新 strict 的兼容别名。评分时重新计算 strict，不信任保存的通过标志；缺人工语义判定计 unknown，不能计通过。`restated_outside` 含无块答案的结果，排除给定输入/控件规则、公式说明与明确示例；HTML 外的计算结果也计入。普通问答和独立 HTML 仍走各自原门槛，其错误列表仅作诊断。

一致性指标沿用23项分母（11展示、6控件、6编辑最终轮），六个编辑初始轮单独复核，不并入分母；retry 仍是原19项子集。v5 新门槛离线汇总使用已保存的 `v5-recheck.jsonl`（43条，当前运行时兼容性复检），retry 使用 `v5-retry-check.jsonl`（26条），不调用模型，也不将复测混入全集。

完整集只跑一次；v5 不重跑失败、不迭代提示词。原始回答、检查结果及分数保留在 `/tmp/ailab-genui-eval/` 供审阅，不写进仓库，也不在本轮结束时清理。其他轮次是否允许唯一失败子集复测，按当次授权执行；不把复测混入全集分母。

离线回归：

```bash
platform/.venv/bin/python -m unittest discover -s platform/evals/genui -p 'test_*.py' -v
platform/.venv/bin/ruff check platform/evals/genui
platform/.venv/bin/ruff format --check platform/evals/genui
```

当前结论见 [2026-10-30 评测报告](../../../docs/progress/updates/2026-10-30-genui-real-model-eval.md)。
