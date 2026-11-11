# 模型菜单：悬停不再跳动，选完不自动收起（2026-11-11）

分支 `fix/model-menu-height`，base `origin/main@81d24699`。改动前的问题见 [ADR 0038](../../architecture/adr/0038-reasoning-effort.md) 的 Portal 一节。

## 问题

模型菜单右边那栏原本跟着指针走。指针移到「离线演示」这类没有档位的模型时，右栏从三档缩成一行「—」，整个弹层从下边缘往上长，模型行被顶到指针下面；指针落到另一行，那一行又把右栏撑高，选择就在两个模型之间反复横跳。

## 改了什么

- Portal（`components/chat/model-menu.tsx`）：右栏只显示**已选中**模型的档位，鼠标悬停和键盘焦点都不再切换；点模型或点档位都留在菜单里，点别处或 Esc 才收起（`usePopover` 原有逻辑）。没有档位的模型显示「—」。
- 高度固定：右栏始终按最高的一份档位列表（`levelRows`）补等高的空行，选中任何模型、任何档位弹层尺寸都不变，行不会从指针下移走。手机布局把档位摊成一行药丸，空行同样占位，所以纵向也不会变。
- `styles/app.css`：「—」改成和档位同一行高，空行复用 `.menu-item` 的高度，删除不再使用的 `.model-item.shown` 悬停底色规则。
- E2E：`select_model` 和思考强度用例改成先点模型再读档位，并在挑选后显式点别处收起；`test_genui_layout` 截图前先收起菜单。

## 验证

代码基线为本分支工作树（包括未提交的改动）。

- Portal：`npm test` 47 个文件 282 项通过（`model-menu` 三条改为「悬停不变、点模型后右栏更新、点档位不收菜单、无档位模型点一下收起」）；`npx tsc --noEmit` 通过。
- Python：`ruff check` / `format --check` 对被改的 `e2e/` 通过（未改产品代码）。
- 浏览器（本机 Playwright 无头 Chromium，`next dev` + 临时页面渲染真实 `ModelMenu`，真实 `globals.css`）：900×700 与 375×760 各一遍，脚本断言指针扫过两个模型前后弹层 rect、模型行 rect、按钮文字逐项相同；点「离线演示」后右栏变「—」而弹层尺寸不变（桌面 150px、手机 161px）；再点回 DeepSeek Flash 和「最高」后菜单仍开着、按钮变成「DeepSeek Flash最高」；点空白处关闭。截图只在 `/tmp`，未入库。临时页面与 dev server 已删除。
- E2E（本机隔离栈：四个真实服务 + Redis 16379/14 + Portal 生产构建，Chromium；`AILAB_VIEWER_READER_PATH` 指向占位文件代替缺失的 office runtime）：`e2e -k "thinking_strength or workbench_offers"` 2 项通过，`e2e/test_genui_layout.py` 5 项通过（含 320/390 宽浅/深色真对话页与 composer 截图）。真实对话页里点模型后右栏更新、点档位后菜单仍开、点输入框才收起均通过。临时 Redis 已关闭，截图在 `/tmp` 未入库。

## 没覆盖到 / 风险

- 本机只跑了上述两项和 genui 布局的 E2E，未跑完整 E2E 与双库全套，其余交 CI。
- 未跑 Portal 生产构建与双库全套，交 CI。
- 有档位的模型里选一个没有档位的模型后菜单仍开着，右栏只有「—」；用户需要点别处关闭。这是有意的（保持一次访问里可以继续换模型），但与改动前「选完就收起」不同。
