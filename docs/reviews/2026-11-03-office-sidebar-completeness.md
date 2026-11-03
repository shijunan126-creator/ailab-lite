# 办公侧栏完整性审查（2026-11-03）

基线：`t3/sidebar-office-file-features@8cbc216`，与已fetch的`origin/main`一致，进入干净。用户要求核查完整性；本轮只审查和维护交接，不修改产品、安装依赖、部署或调用模型。

结论：四格式原生侧栏、显式选区引用/直接提问、模型定位与截图主链路已实现并有历史验收；仍有模型指导与在线视图生命周期缺口，不能称完整收尾。

后续状态：用户已授权修复，以下为修复前审查；当前实现、检查和保留的间歇失败见[修复记录](../progress/updates/2026-11-03-office-sidebar-fixes.md)。

## 确认的缺口

1. **模型可读技能仍禁用已实现能力。** `vendor/office-suite/patches/001-tools-and-roots.json` 把四格式viewer指导替换为`planned:P3`，称open/reveal/get_view/capture “not currently callable tools”，要求告知用户viewer不可用。文字已进入四份`images/sbx-office/skills/platform.*/viewer/SKILL.md`，导航/modules.json允许Chat/Work加载；真实工具已注册为`document_view`。可能影响模型获取“这里/选中内容”，不是已证明每次回答都会失败。应修有序patch并重建，补录制请求及真实模型自然提问验证，不能单改生成文件或只手工指定工具。
2. **仍在线但不操作的视图会失效。** runtime `ui/main.tsx:292`只在doc/location/selection变化后report_view；父桥`components/preview/office-view.tsx:179`去抖600ms，没有续期。`api/view_state.py`的TTL=600；get_view/viewport capture只查未过期状态。因此同一选区保持超过10分钟、面板仍打开时，get_view返回unavailable，viewport capture也无法选择该视图。4秒check_document轮询只查版本，不续期。需要有界在线续期或按请求取新快照，兼顾隐藏/关闭/断线，不能靠无限延长TTL保留离线视图。
3. **能力账本仍是早期状态。** `vendor/office-suite/inventory.py`生成的`capabilities.json`仍有planned:P1/P2/P3/P4及“Source pinned only”等说明；资源测试验证生成一致性，不证明逐能力已验收。需要逐项链接当前证据，保留真正blocked/未验项，不批量改成implemented。

## 当前检查与历史边界

- 用Python标准库提取并执行当前`read_module`函数，以仓库技能为catalog：四格式Chat返回complete=True，均含“not currently callable tools”，planned:P3分别11/13/11/10处。未调用模型，不推断实际失败率。
- 执行当前view_state内存后端函数（隔离settings、可控时钟）：初始1条状态，601秒后0条；结合前端上报条件确认缺续期。未启动浏览器等待10分钟，未做Redis或部署环境复现。
- 普通主输入框submit不自动附选区；paneAsk显式传docRefs，添加按钮走quote/outbox。这是当前交互合同；“选中后每条普通消息自动带引用”没有实现。get_view可供模型查询，但受上述两项缺口影响。
- 既有八组Chat/Work四格式E2E覆盖添加→主输入框发送→引用定位→截图；直接ask有前端桥测试及XLSX手工fixture证据，不等于四格式自然语言/直问全链路均独立验收。
- 历史完整套件、模型、资源证据见[P5最终记录](../progress/updates/2026-11-02-office-suite-p5.md#p5-最终修复与联合检查2026-11-03)与[Linux修复](../progress/updates/2026-11-03-office-linux-e2e.md)。移动端多浏览器真实合流、复杂Office保真、Windows/VBA、长期大并发仍未全面验收。410旧截图恢复已有后续故障注入证据，不沿用早期“完全未修复”结论，也不称生产端到端已验。
- 本工作树无Python venv/Portal node_modules；未安装或重跑套件，不把历史结果当当前测试。本轮仅文档变更，最终检查diff与引用。

下一步：优先统一模型指导和真实工具合同、补在线视图生命周期，再集中验证四格式直接提问/添加引用/普通消息询问当前选区和长时静止视图；按证据更新能力账本。本轮核查不自动开展修复或部署。
