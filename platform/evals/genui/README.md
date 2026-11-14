# GenUI 真模型评测

固定中文用例在 `cases.py`，覆盖数据展示、控件、HTML、不应出 UI、两轮编辑和主动使用 UI。真实运行会调用付费模型，必须获得当次授权；评测工具只调用 `ui_components`，调用其他工具记为失败。

原始回答、检查结果、分数和中间文件只写到仓库外。默认输出目录是 `/tmp/ailab-genui-eval`，同 label 的已有回答不会覆盖；新运行换 label，审阅完成前不要删除。

## 运行

从仓库根执行：

```bash
platform/.venv/bin/python platform/evals/genui/run.py --label <label>
OPENUI_TELEMETRY_DISABLED=1 node platform/evals/genui/check.ts /tmp/ailab-genui-eval/<label>.jsonl \
  > /tmp/ailab-genui-eval/<label>-check.jsonl
platform/.venv/bin/python platform/evals/genui/score.py \
  /tmp/ailab-genui-eval/<label>.jsonl /tmp/ailab-genui-eval/<label>-check.jsonl \
  --judgments platform/evals/genui/judgments-<label>.json
```

`run.py` 默认读取 `platform/models.toml` 的 `deepseek-v4.1-flash`，并从进程环境的 `AILAB_KEY_DEEPSEEK` 取密钥。其他提供方或隔离部署使用：

```bash
platform/.venv/bin/python platform/evals/genui/run.py \
  --models-file <private-models.toml> --env-file <private-env> --model <model-id> --label <label>
```

密钥变量由模型条目的 `api_key_env` 指定，名称以 `AILAB_KEY_` 开头；`--env-file` 应提供同名变量。密钥只进入进程环境；不要创建或提交密钥文件，也不要打印 provider 异常正文。`--models-file` 的模型 `id` 必须与 `--model` 一致，别名不会自动替换部署里的模型。

检查器复用 Portal 的拆分器、目录、OpenUI parser 和 evaluator，通过现有 `platform/portal/node_modules` 运行，不另装依赖。先确认 `check.ts` 成功产出检查行，再跑 `score.py`。

## 判定

结构门槛包括：代码块闭合、`Stack` 根、无解析/属性错误、无未知组件、无默认求值错误、HTML/fallback 非空。Table、LineChart、BarChart、PieChart 还要通过 Portal 的取值函数和控件探针；结构合法但显示错误会计入失败。

语义门槛来自人工判定文件：`wrong_statements` 为短引文列表（无错误为 `[]`），`restated_outside` 记录块外复述，编辑轮另有 `edit_success`，HTML 另有 `html_success`。缺少判定键是 unknown，不能算通过。判定文件必须与用例集一致；旧 judgments 没有覆盖的新用例不要直接沿用。

评分器区分结构合法率与严格一致率。严格数据分母是除 `no_ui`、`html`、`proactive` 之外的场景；编辑只计最终轮，初始轮分开复核。数字字面量检查只产生人工核对候选，不能证明计算或业务结论正确。手工正确的数字、公式说明或块外复述不应仅因“出现过新数字”判错。

真实运行一次后先审阅回答和工具轮次，再决定是否允许唯一定位失败子集的复测；不要把复测混入全集分母，也不要为了让指标变好而重复跑同一完整集。

## 离线回归

以下检查不发真实请求：

```bash
platform/.venv/bin/python -m unittest discover -s platform/evals/genui -p 'test_*.py' -v
platform/.venv/bin/ruff check platform/evals/genui
platform/.venv/bin/ruff format --check platform/evals/genui
```

`test_harness.py` 覆盖工具往返、轮次上限、检查器负例与评分器契约；`test_score.py` 覆盖严格语义、结构/控件/HTML/编辑门槛。它们只能证明评测工具没有退化，不能代替真实模型质量、渲染效果或费用结论。
