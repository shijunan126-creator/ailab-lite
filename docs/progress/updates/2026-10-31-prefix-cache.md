# 前缀缓存命中修复（2026-10-31）

基线 `origin/main@f316b85`。用户反馈前一晚测试对话缓存命中可能有问题，要求优先参考 DeepSeek Harness。决定与依据见 [ADR 0030](../../architecture/adr/0030-prefix-stable-model-requests.md)。

## 定位方法

部署库在用户 Mac 环境，本机未读取线上数据。改为离线录制：经真实 API（TestClient + ASGITransport）跑三轮 Work 对话，录下每次主模型请求的 `messages`/`tools`，逐次比较公共前缀。修复前：发现工具后 `tools` 变化（工具段之后的全部消息无法复用）；第三轮请求改写了第一轮回复（GenUI 占位）。

## 修复与验证

- 工具：完整目录在模型 15% 输入预算内时固定提供（`discovery.offer/full_fits`）。
- 历史：Agent/API 三处 `for_history` 一律 `keep_ui=True`。
- 上下文：`turn_context()` 生成的 `<ailab_context>` 挂在当轮 user 消息；记忆刷新兼容新旧位置（含多模态 list 内容）。
- `tests/test_prefix_cache.py`：运行内每次请求都是上次请求的严格延伸（工具相同、消息前缀相同）；三轮对话首个请求工具一致、历史渲染跨轮一致且保留 GenUI 原文。修复前 2 项均失败，修复后通过。完整套件对比见 PR。

## 未验 / 下一步

- 真实 DeepSeek 命中率：部署后按 run 比较 `model_usage` 的 `cached_tokens/prompt_tokens`，或在用户 Mac 部署上复测同一组对话。
- 每轮仍需重新 prefill 当轮 user 消息（含约 600 token 的记忆规则）与上一轮最终回复，这是重建历史的固有成本。
