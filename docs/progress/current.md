# 文件输入：剩余边界修复完成（2026-11-07）

- 最新授权：用户先要求PR前复核并修复Chat/Work提示词与调用逻辑，随后要求把剩余边界“能修的都修”，可用真实模型测试。仅在本地实现、验证和提交，没有push/PR/合并/部署。分支`feat/document-input-lifecycle`，代码head `766ccfcc`，本地base `origin/main@fdc0a05f`，本轮未fetch。
- 已完成：
  - 第一轮：指南只写各格式事实，`region_ref`，字段级参数反馈，多页OCR执行身份。
  - 第二轮：
    - 小PDF矢量按几何分类后可内联；
    - Excel可按工作表名查看渲染页；
    - PPT形状带页面位置，inline页图带区域身份；
    - 搜索单批按工作量限界（25次调用降到4–5次）；
    - 模型exec在所有后端统一为bash；
    - Work目录预建；
    - 本地对象目录并发首开时有界重试；
    - 修了定时任务测试的时钟依赖。

  根因与数字见[终审修复](updates/2026-11-07-document-prompt-review.md)。
- 验证：
  - Ruff通过；
  - PostgreSQL/Redis双库全套2739通过/69跳过；
  - SQLite全套2567通过/240跳过，唯一失败为定时任务时钟依赖，已修复，单独重跑通过；
  - E2E 194通过/15跳过；
  - Portal 258个测试通过，build通过；
  - 真实DeepSeek Flash终跑加复测，追问缓存96.92%–99.42%。
- 剩余边界：
  - 含填充框的小PDF仍不内联；
  - 按表名做OCR未实现；
  - 模型偶有行名标错、看页后多做一次OCR；
  - 只有DeepSeek Flash的凭据，未做多模型评估；
  - 未覆盖真实业务文档、Portal浏览器路径和NPU/S3/OpenSandbox真后端。
- 下一步：用户决定是否提PR。PR正文按AGENTS §11链接两份dated update并写明上述边界；CI需在最终head跑。临时VM和运行产物已清理。
