# 技能广场精选内容（2026-11-01）

## 范围与基线

`origin/main@7f71d30`；分支 `t3/curated-office-skill-market`。仅后端和内容；保留 GET 列表及 POST 安装 API，不改 Portal、不部署、不迁移数据库。旧交接中的其他工作树任务不属于本轮授权。起始无关 Portal 跟随滚动提交保留于 `preserve/market-original-portal-follow`，未纳入本 PR。

## 结果与取舍

八个包：`work-report` 周报/月报（写作）、`meeting-minutes` 会议纪要（写作）、`contract-review` 合同审阅要点（法务）、`data-analysis-report` 数据分析报告（分析）、`competitor-analysis` 竞品分析（分析）、`hiring-kit` 招聘JD与面试题（人事）、`official-writing` 公文/通知写作（写作）、`speech-writing` 演讲稿/致辞（写作）。均为 AILab 原创、Apache-2.0、1.0.0、非内置。

内容聚焦业务工作流、来源与不确定性、可执行产出和交付检查，不复制四格式基础技能；默认支持对话正文，文件生成按需使用内置平台技能，因此 `requires=[]`。合同内容定位业务初审，需法务核实法律结论；竞品研究不编造当前价格或来源。

`market_skills/*/SKILL.md` 显式列入 setuptools package-data。现有 Python 服务 Dockerfile 已 COPY 整个 `ailab_platform` 并 pip install，无需增加镜像步骤；Work 镜像不应预装个人市场技能，安装继续走 pending 文件与 Worker 审核。八份源 Markdown 合计 13,950 bytes，无脚本、新依赖或后台作业；列表固定八项，ZIP 仅安装时生成，无新增常驻计算开销。

## 验证

测试代码基线 `eb1ab44`（base `7f71d30`，后续仅补文档结果）。

- `ruff check .`、`ruff format --check .`：通过。
- `pytest tests/test_market_api.py tests/test_personal_skills.py -q`：15 通过；覆盖全部包与 frontmatter 对齐、Worker 共用扫描器兼容、真实安装路由 pending/去重/用户隔离/匿名拒绝及旧格式入口 404。
- `uv build --wheel --out-dir /tmp/ailab-market-wheel platform`：通过，检查 wheel 包含全部八份 SKILL.md。
- 完整 Python 套件 `pytest tests -q`：一次完成，exit 0，1931 通过、163 跳过。macOS 使用 GNU coreutils/findutils PATH，按 CI 构建 vendor Office reader；未配置的外部后端契约按规则跳过。无全套重跑。
- golden 结构自检：2 份 fixture 通过，不代表真实模型或视觉质量验收。

未做真实模型内容质量评测、实际容器构建和部署验收。Portal 未改，本机不另跑 Portal build/浏览器；PR CI 按既定规则覆盖前端构建、双库与 E2E。status-matrix 无独立市场能力行，本轮不新增状态分类。

## 下一步

[PR #140](https://github.com/shijunan126-creator/ailab-lite/pull/140) 已创建；等待 CI 全绿后 squash merge，不部署。
