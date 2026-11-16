# ADR 0038 · 思考强度：按模型配置，按回答固定
状态：已采纳（2026-11-11）。

## 背景

DeepSeek V4 支持 `reasoning_effort`（实际只有 low/high/max 三档，默认 high）。平台此前不发这个字段，所有回答都按供应商默认的 high 思考。用户要求：模型旁边可选思考强度，每个模型的档位不同、单独用配置文件配置；平台默认 low，记住用户上次的选择。

## 决定

- **配置**：`platform/reasoning.toml` 按模型系列定义档位（id、显示名、合并进请求体的字段）和默认档；`models.toml` 的模型用 `reasoning = "<档位组>"` 引用。同一模型挂在不同网关（官方 / OpenCode）共用一组档位。档位不能改写请求框架字段（model/messages/tools/stream/max_tokens 等）。文件放在目录旁边（部署时同一个 ConfigMap / 镜像目录），否则用仓库内的。模型引用不存在的档位组时启动失败，不静默忽略。
- **API**：`/v1/models` 只给出档位 id 和名称，不给上游字段。新会话、发消息、排队、重试、编辑都接收 `reasoning`；校验后写进 `run.options.reasoning`。不传时用默认档；模型没有档位而传了值返回 400。重试/编辑不换模型时沿用原回答的档位，换到没有档位的模型时丢掉；排队消息带着排队时的档位；插话沿用正在进行的回答的档位。
- **Agent**：一次回答的所有调用（主调用、压缩摘要、收尾）都带 Run 的档位；关闭思考的安静调用（标题等）不带。后备模型没有该档位时用它自己的默认档。
- **Portal**：模型菜单分两栏，左边模型、右边**已选中模型**的档位；悬停不切换（悬停换栏会改变弹层高度，把行从指针下移走，选择随之反复跳）。点模型或档位后菜单不自动收起，点别处或 Esc 关闭；没有档位的模型右栏显示「—」。按钮显示「模型名 档位」。每个模型的上次选择存浏览器（`ailab.reasoning`，与 `ailab.model` 同一方式）。回答下方的模型标注带上当次档位。

## 前缀缓存

真实模型（OpenCode 网关，deepseek-v4.1-flash）：同一档位的后续轮次 cached/prompt 96–97%；换档后的那一轮 cached=0，再换回另一个档位也从 0 开始，说明供应商按档位分开缓存。所以档位必须在一次回答内固定（包括摘要等旁路调用），只有用户主动换档时多一轮冷启动，与换模型同类。档位不进系统提示、不改消息，不需要上下文边界。

## 后果

- 默认从供应商的 high 改成 low：同样问题思考 token 明显减少（12 球称重题：low ≈2.8k、high ≈5k、max ≈8k），回答更快、额度消耗更少；需要深度推理时用户可调高。定时任务和旧客户端的请求用默认档。
- 无迁移；`run.options` 多一个键。部署需要同时更新镜像（含 `reasoning.toml`）和 Portal；k8s 的 ConfigMap 由 `k8s_models.sh` 一起发布两个文件，自定义目录（`models.local.toml`）要自己加 `reasoning = ...` 才显示档位。

## 供应商与关思考档位的补充

- **请求形状由 provider 决定，档位字段由 reasoning.toml 决定**。`platform/ailab_platform/model_providers.py` 定义三个 provider 的默认请求形状：`opencode-go`（`x-opencode-session` 头、回传 `reasoning_content`、quiet 调用关思考）、`deepseek`（回传、quiet 关思考）、`openai`（通用兼容、无默认差异）。`models.toml` 的逐模型字段仍可覆盖 provider 默认值。传输层与调用逻辑不变，只按 provider 提供配置默认值。
- **关思考是推理档位的一种**：DeepSeek 与 Hy4 的 profile 都含 `none` 档，body 为官方 OpenAI 格式的 `thinking.type=disabled`，与 quiet 短调用的关思考字段一致。`reasoning_effort` 只表达强度（low/high/max），不用它表达关闭。用户选 none 时主回答不思考；quiet 短调用按调用性质自动关思考，两条路互不覆盖。
- **验证以 `reasoning_tokens` 为准**：opencode 网关对未知字段静默忽略并返回 200。实测量子：`reasoning_effort=none` 与 `thinking.type=disabled` 使 Hy4/DeepSeek 的 reasoning_tokens 归零；`reasoning_effort=high/low` 只证明思考开启，Hy4 的 high 相对默认强度提升未证实；`enable_thinking=false` 对本网关无效。改字段后必须用真模型核对 usage，HTTP 状态不足以证明字段生效。
