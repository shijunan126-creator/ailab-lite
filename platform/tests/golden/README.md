# 黄金样例

`run.py` 用仓库自带的确定性 Office 构建器检查 PPTX/XLSX 结构，不调用模型、不联网。它证明内置样例仍能生成，不代表公司模板、渲染效果或业务正确性。

从 `platform` 运行：

```bash
.venv/bin/python tests/golden/run.py --self-test
```

CI 在 `quality` job 执行同一命令。检查失败返回非零并阻止合并；它覆盖页数、显式字体、形状/页面坐标、工作簿数据、`SUM` 公式与缓存错误，不检查字形适配、页面溢出、公式重算或数据含义。

已经有外部生成结果时，可以只做结构检查：

```bash
.venv/bin/python tests/golden/run.py --outputs /tmp/<owned>/outputs --report /tmp/<owned>/report.json
```

调用者负责准备正常的 Work 流程、模型额度和输入；产物与报告放在仓库外。不要把密钥、私有输入或真实用户输出写进本目录。公司模板和真实模型质量需要另行提供批准过的模板、预期产物和费用上限。
