# 思考强度选择与基础技能收起（2026-11-11）

分支 `feat/reasoning-effort`，base `main@323184b1`。决定见 [ADR 0038](../../architecture/adr/0038-reasoning-effort.md)。

## 做了什么

- 模型菜单加思考强度（DeepSeek：低/高/最高，默认低），档位在 `platform/reasoning.toml`，按模型记住上次选择；回答标注当次档位。
- 技能页「平台技能」里四个始终开启的 Word/Excel/PPT/PDF 收成一行「基础技能」，点开才列出；可开关的「PPT 设计」照常显示。
- 修复深色模式下所有菜单悬停行不可见：`--subtle`（#212124）几乎等于弹层底色（#222225），新增 `--popover-hover`。

## 验证

本地（代码基线为本分支工作树）：

- `ruff check` / `format --check` 通过；`tests/test_reasoning_effort.py`（档位解析、请求体、API 各路径）、`tests/test_catalog.py`、`test_prefix_cache.py` 新增录制请求测试（摘要调用与主调用同档位）通过。
- Portal：`tsc` 通过；`model-menu`、`outbox`、`offered-skills` 单测 14 项通过。
- 浏览器（隔离 E2E 栈，Chromium）：新增 `test_thinking_strength_is_picked_beside_the_model_and_remembered`、模型列表、技能开关三项通过；1280/390 宽、浅/深色截图检查菜单、按钮和技能页（截图在 `/tmp`，未入库）。
- 真实模型（OpenCode 网关，`/tmp` 临时脚本，已删除）：经 `ModelTransport` 发 5 轮，low/low/high/high/low 的 cached/prompt 为 0、96.4%、0、96.7%、0。换档那一轮全冷，同档位续轮正常命中。同一道称重题思考 token：low ≈2.6–3.1k，high ≈4.0–6.0k，max ≈7.5–8.6k，确认网关透传 `reasoning_effort`。

完整双库、E2E、Portal build 交 CI。

## 没覆盖到 / 风险

- 换档的那一轮没有缓存（供应商行为），只在用户主动换档时发生。
- 官方 DeepSeek 端点账户余额不足，未直接验证；按其文档字段一致。
- 本机自定义目录 `models.local.toml` 需加 `reasoning = "deepseek-v4"` 才显示档位（部署时处理）。
