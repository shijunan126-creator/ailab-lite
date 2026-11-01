# 工作文件查看与搜索错误语义（2026-11-01）

## 范围与基线

用户授权调查两个本机故障，后端修复、回归、一个 PR、CI 绿后 squash 合并；不修改 Portal，不部署。分支 `t3/workspace-search-errors`，base `21f3300`。调查路径为真实 `/Users/michael/AILabDisk/local`。收到任务时称后端为 `8890338`；现场实际四个后端均为 `release-21f3300`、Portal 为 `release-8890338`，Office 为 `release-277a8c4`。查看相关文件与 `8890338` 无差异。

## 搜索：已确认的失败与修复

新闻会话的 Run 事件记录 6 次中英文 `web_search`，全部 `ok=true`、`sources=[]`。同一时期 SearXNG 日志显示 Wikipedia、Brave、DuckDuckGo、Google CSE 等引擎连续网络超时，部分另有 429；不是 query 语言问题、Agent SSRF 过滤、secure profile 拒绝或未配置 provider。现场配置为 `searxng`，指向内部服务，JSON 格式已启用、使用默认无需自备 API key 的引擎；Compose 未配置搜索出口代理。

代码只检查 HTTP 状态并读取 `results`，忽略了 SearXNG 在 HTTP 200 中的 `unresponsive_engines`。这是将上游故障包装成“成功空搜索”的根因；现在无结果且存在失败引擎、显式 error 或非法响应会抛 `WebError`，既有工具链转换为失败结果。真正空结果仍成功，部分引擎失败但已有结果仍可使用；异常不再回显内部请求 URL。

当前只读同参数现场请求（英文新闻，`language=zh-CN`、`safesearch=1`）返回 HTTP 200、42 条、`unresponsive_engines=[]`，说明历史出口故障已经恢复。没有证据要求更换密钥或修改仓库配置，未写本机配置。若复发，应修复 SearXNG 容器到公网引擎的连通性，再核对 `/search?format=json` 的失败引擎；仅改 Agent 超时或语言不能修复它。历史网络故障具体属于哪一个外部跳点，现有日志无法再细分。

## 工作文件：确认范围与未解决边界

PPT Run 中首个四页 `[1,2,4,5]` 成功，随后 9 次失败涉及四页、两页乃至单页；失败耗时约 4.7–6.4 秒，同一文件的 `[9,10]`、`[4]` 又成功，不能归因于四页上限或 45 秒执行 deadline。自动预览各版均显示 11 页。

历史事件只保存通用 ClientError，旧 Broker 容器日志随其他部署被替换。初次用最终交付文件和原 Office 镜像重放成功；用户随后明确批准真实模型测试，改从 gzip checkpoint 中取完整 `write_file` 规格（公开 tool event 会截断正文），恢复首次失败前版本，真实模型 Run `run_26c80657af614ca691d5` 成功复现约 5.2 秒 422。后续保留测试沙箱并独立调用原 Broker，抓到原始 `view_failed`。

实际失败链路是 Work 自己的沙箱（不经过 Worker）：LibreOffice 热监听器导出时 RSS 超过 `render_work_listener_rss_bytes=402653184`，监督器在导出中途终止它。临时诊断记录 RSS 403570688–457363456 字节、距 activity 更新不足 0.6 秒，排除 idle 回收；UNO 抛 `Binary URP bridge disposed during call`。随后冷转换复用了中止导出的输出目录，即使删掉 `input.pdf`、换了 profile，LibreOffice 仍可能 exit 0 但打印 `SfxBaseModel::impl_store ... Io Abort Code:27`，没有生成 PDF，helper 最终抛 `LibreOffice produced no pdf for input.pptx`。因此相同文件和页码可间歇成功，减少页数不解决 Office 整文档转换问题。

修复保留原 RSS ceiling 与单次冷回退，不扩大资源或加重试循环；监听器失败后改在原输出目录下新建独立恢复目录，冷转换不再继承中止导出的输出命名空间。该目录随既有 render job 临时目录清理。另贯通 helper→Broker→模型的白名单原因码，提供安全中文原因与建议，不透传原始异常、宿主路径或命令；失败仍在既有 `agent.tool_result` 中记录。

真实模型验证 Run `run_2d09f64227f248b58beb`：新建测试沙箱中临时替换本次 helper（不是部署服务），原规格生成 11 页后，八组页码全部 `ok=true`；额外通过原 Broker 连续查看 12 次全部成功。诊断再次捕获 UNO bridge disposed，但出现 `FRESH_RECOVERY_DIRECTORY` 后命令 exit 0，证明执行了修复的冷恢复路径。资源上限、Office 镜像和运行服务均未修改。其他测试 Run 仅用于保留现场；不修改原用户对话。未做多用户容量或长期压力验收。

## 验证与成本

- Ruff 全库 lint / format、导入边界与 skill 清单检查通过。
- 相关 `test_web.py`、`test_workspace_view.py`、`test_net_tools.py`、`test_reader.py`：86 通过；之后补充的显式 provider error 用例纳入完整套件。
- 新回归在旧实现下分别会出现：失败供应商被返回 `[]`、Broker 丢失码、模型只收到通用文案。还覆盖合法空响应、部分成功、非法响应，以及未知 detail 不泄漏。
- 完整 Python 首轮 1927 通过、163 跳过、18 失败：本 worktree 未构建 vendor Office reader；按 CI 执行 `npm ci --ignore-scripts` + `npm run build` 后只重跑 18 个失败项，全部通过。真实模型定位后新增恢复修复与回归，相关 39 项通过。最终产品基线 `46450f9` 全套 1944 通过、163 跳过、2 失败：旧 UNO fixture 写死原输出目录，已更新为从实际命令读取目标；另一个 `test_pre_output_fallback_has_distinct_calls_and_real_ledger[429]` 偶发账本行数 3 而非 2，未改相关产品代码。只重跑 `test_uno_render.py` 与 `test_model_gateway_context.py`，24 项全部通过；CI 结果见 PR。
- 未运行浏览器或本机 Portal build（无 Portal 改动）；CI 按既定流程运行。未连接业务 DB 跑测试；现场 DB 仅只读查询。未部署。
- 无新依赖、后台任务、数据库查询或迁移；成功路径增加常数次响应结构检查，失败路径只处理有界 stderr 尾部，并新增一个临时恢复子目录。渲染与网络资源限制保持原值。临时文件和原始输出仅在仓库外，调查结束清理。

PR：[后端修复 #141](https://github.com/shijunan126-creator/ailab-lite/pull/141)，等待最终 CI，绿后 squash。仅测试夹具与文档在最终全套后调整，未再改产品代码。
