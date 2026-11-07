# 文件输入：PR #161 已合并（2026-11-08）

- 最新授权：用户在 PR #161 上问预览开关是否会让页码错位；同意按“每种格式一套编号”修复后，要求提 PR、合并并部署最新版。PR head `4338d69d` 的 13 项 CI 全绿后 squash 合并为 `main@2bf86144`。
- 本轮完成：Word 页图只走服务端；PPT 服务端导出包含隐藏页；修复 CI 的两个根因（Portal `jszip` 锁文件不一致、`get_storage` 关闭后并发首次创建的竞争）。根因、验证和风险见[页码一致性记录](updates/2026-11-07-document-page-numbering.md)。
- 验证：Ruff 通过；PostgreSQL/Redis 全套 2739 通过 / 69 跳过；E2E 193 通过 / 15 跳过，唯一失败是旧的 Word 浏览器截图期望，已改并重跑通过。
- 剩余：已渲染过、并且含隐藏页的 PPTX 不会自动重渲（页图缓存没有渲染器版本）；本机现有 PPTX 都没有隐藏页，不受影响。listener 转换路径已在合并后的 sbx-office 镜像里实测；Portal 浏览器路径没有真实跑过。
- 下一步：等用户使用反馈。若要处理旧 PPTX 页图缓存，先给 `RenderedPage` 和文件预览元数据加按格式划分的渲染器版本。

## 上一轮交接（2026-11-07 剩余边界修复）

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
