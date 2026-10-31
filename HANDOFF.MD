# 前缀缓存陷阱（2026-10-31）

压缩不能丢掉当前轮原始用户输入及已确认交付围栏；近期工具组要保持配对，recall 正文在压缩边界替换为固定提示。recall 来源仅在 run 内首次使用时验证，run 中途撤权到下一 run 才检测，恢复同一 run 保留验证缓存。

同一上下文段内的请求必须只追加到前一次请求；刷新旧块、重跑工具、改变工具集合或重新裁剪任何较早字节，都会让后续上下文重新计价。权限撤销和压缩需要显式边界，不能为了缓存跳过授权复验。

# HANDOFF

- runsc 15 页 PPTX 探针中 LO profile 使 tmpfs 比预热基线增加 8192 字节；健康检查按设计释放该沙箱。不能仅提高阈值让它复用：须先证明残留内容已清除且监听器仍健康。

- Debian Office 镜像的 `soffice` 是启动脚本，会派生 `soffice.bin`；监控启动脚本的 RSS 不能约束真实转换器。Work listener 直接启动并监督 `soffice.bin`，runsc 中低阈值和空闲退出探针已验证。

- runsc 容器启动后 `docker cp` 的 overlay 文件可能对容器进程不可见；探针通过 `docker exec -i` 写入容器内文件。UNO listener 真实探针两次转换成功，但 soffice 进程 RSS 约 274 MiB，不能用目标值代替实测容器总 RSS。

- 渲染日志旧版 `acquire_seconds` 包含 `inject_seconds`，相加会重复计算；C3e 起分别记录且标记 standby/cold。一次并发样本和整批发布的 ready 时间不能当作三次中位数或逐页首屏证据。

- Docker sandbox 的 supervisor 在创建时固定最长生存期；standby 的提供者上限须覆盖空闲期加一次任务，认领后业务租约仍保持短 TTL。LibreOffice 仅复用 profile 不代表进程常驻，热查看延迟仍须实测；详见 [C3d](docs/progress/updates/2026-10-31-prefix-cache-render.md#c3d-预热渲染)。

- Codex 沙箱内 `bin/docker.sh` 可能因 `ps` 检查受限误判磁盘状态；指定 `DOCKER_HOST=unix:///Users/michael/AILabDisk/local/colima/ailab-verify/docker.sock` 和 `DOCKER_CONFIG=/Users/michael/AILabDisk/local/docker-client` 直接调用 Docker，socket 受限时在获准环境执行。该 daemon 与个人栈共享，测试仅操作独立 `c3-*` 资源。

- GNU PATH 必须 `export` 给整个 pytest 进程树；只对 `ruff` 命令前置赋值会让后续 MockSandbox 使用 macOS `stat/find`，产生与产品无关的失败。C3b 并行解析与渲染在 2 CPU 下可能竞争租约和 CPU，必须实测首屏及完整耗时，不能假设并行更快。

- 共享 Docker daemon 上的独立 Broker 必须使用唯一 `AILAB_BROKER_OWNER`；同 owner 的现有 Broker 会认领测试容器并按不存在的 run 回收，不能把退出码 137 直接判为 OOM。无租约标签的一次性容器可用于单机脚本测量。

- macOS 的 bsdtar 默认可能为带扩展属性的图片加入 `._` AppleDouble 成员；预览归档校验必须禁止这些额外成员，打包时设置 `COPYFILE_DISABLE=1`。完整 C3 边界见 [记录](docs/progress/updates/2026-10-31-prefix-cache-render.md)。


## 2026-10-31 Super-agent 借鉴与前缀缓存

- 前缀缓存：供应商按“最长已见前缀”命中，工具 schema 渲染在所有消息之前（DeepSeek 模板还会把所有 system 消息提到开头）。任何按位置/分页决定的渲染（如“只有最后一轮保留 GenUI”“每页最后一条”）都会改写更早的轮次；运行中改变 tools 数组会让整段失效。改请求结构前先跑 `tests/test_prefix_cache.py`，它录下每次请求比较前缀。依据与 dsh 做法见 [ADR 0030](docs/architecture/adr/0030-prefix-stable-model-requests.md)。
- （2026-11-01 合并后已取代）#117 曾把每轮会变的上下文放到当轮 user 消息的 `<ailab_context>`；合并 `fix/prefix-cache-render` 后改回边界 system 消息 + 运行中追加变化，见前缀缓存设计与 ADR 0030 状态。不要再假设 `<ailab_context>` 存在。
- 子任务（`run_subtasks`）不能从 schema 删除工具，只能执行时拒绝，否则 fork 前缀失效。watchdog 判定请求也重放同一消息与工具；测试 conftest 默认 `AILAB_WATCHDOG_MODE=off`，否则脚本模型会被判定调用消耗回复。
- `tests/test_document_context.py::test_document_indexes_are_bounded_at_100000_blocks` 在 PG 上偶发选中另一索引（EXPLAIN 断言），与改动无关时重跑。
- 本机自动模式会拦截 `kubectl exec` 读取 Pod 环境变量（含 DB 连接串）；查线上缓存命中要让用户提供数据或走正常 API。

## 2026-10-31 记忆与本机部署

- 依赖等待必须由 reconciler 持续重驱动，不能只依赖发起请求的一次尝试：无关的 pending job 可能触发 guard，使该请求被跳过。

- 立即整理启动后不能仅凭瞬时 active=false 判断完成，应等本次记录创建并达到终态；否则可能误读上一次结果。中文语义去重历史根因与修复见 [P5–P6 记录](docs/progress/updates/2026-10-31-memory-js-polish.md)。

- `ailab-disk status` 在沙箱内可能因禁止 `ps` 失败，`vm.sh` 随后误报 SSD 未挂载；先在可访问进程状态的环境核实，不能据此 attach/recover。记忆评测须走 evaluation job 和正常计费，不应用真实用户记忆作为输出目标。
- 真实 episode 回放必须核对 `_fit_episodes` 实际送入的片段；按 dream 的 run ID 取回 episode 可能超过原批次且被输入预算截断。单次新旧模型回放的旧版没有重现历史误提取，不能将零误提取解释为确定性修复。

## 2026-10-30 Skills / memory 集成

- 恢复个人技能的冻结版本必须以当前 Run 的 checkpoint 精确绑定并重新核对用户、删除和审核策略；仅依赖当前技能 head 会在更新后卡住沙箱。读取和解包必须有大小上限，失效时警告并继续。
- `0042` 降级前按用户和本地日期确定性保留一条记录；同日多批是正常数据。当前没有外键引用 `memory_dream_runs`。
- Prompt 稳定系统段与动态项目/记忆段分别位于首两个消息；测试历史、恢复图片和摘要时不能继续假设用户消息紧接第一段系统提示。`client_calls` 测试清理须先于 runs。
- 浏览器 QuickJS（`@jitl/quickjs-wasmfile-release-sync`）的 `runtime.setMemoryLimit` 不生效（该构建拿不到 `malloc_usable_size`），死循环分配能涨到数百 MB。硬上限要用 `newVariant(variant, { wasmMemory: new WebAssembly.Memory({ initial: 256, maximum: 256 + MB*16 }) })`；超限后丢弃整个模块重新加载。CSP 需 `'wasm-unsafe-eval'`。
- Portal 里被 `tests/*.test.mjs`（node-test-compat）加载的文件只能单文件转译，不能有相对 import（如 `lib/work-process.ts`），否则报 `Failed to resolve module specifier`；共享常量放在被加载的文件里。
- 用 `git hash-object -w` + `update-index --cacheinfo` 分批暂存同一文件的部分改动时，路径必须相对仓库根；在 `platform/portal` 下执行会在根目录生成错误的索引项。
- Codex 委派：`--approve-for-me` 不能与 `-s` 同用；`codex exec resume` 没有该参数和 `-C`，用 `-c sandbox_mode=… -c approval_policy=…` 并在 worktree 目录里运行；需要写 `.git` 时加 `--add-dir <repo>/.git`。
- Portal Skill `kind` 是 `base|builtin|personal`，个人文件格式是 `kind_file`；旧 Skills 页面按 `kind == md|zip` 比较会使生产构建失败。E2E 的 composer 是 contenteditable，技能 chip 不能用 fill 覆盖。

## 2026-10-30 收起侧栏与手机字号

- 收起 Grid 侧栏必须将列宽变量 `--side-w` 设为 0；只隐藏侧栏不会归还列宽。
- 手机文本字段必须至少 16px，避免 iOS 聚焦缩放；统一规则在 app.css，不要设置更小字段字号并期待它在手机生效。
- 手机字号统一来自 tokens.css 的媒体块，不新增组件级手机字号。

## 2026-10-30 按钮与固定菜单定位

- 不要用 `transform` 定位按钮：全局 `button:not(:disabled):active { transform: none }` 会在按压时取消定位。改用 inset/margin，或独立的 `translate` 属性。固定菜单统一经 `placeMenu()` 定位，并保留 `.pop-menu.fixed { min-width: 0 }`：基础 `.pop-menu` 的 `min-width: 200px` 会覆盖较小的内联宽度，造成锚点偏移。根因与回归见[记录](docs/progress/updates/2026-10-30-menu-motion.md)。

## 2026-10-30 PR #108 CI 时序隔离

- 共享测试数据库里的 outbox 会被 API lifespan dispatcher 扫描；只替换 `preview_dispatch.get_queue` 无法隔离缩略图测试，后台 tick 会发布其他测试遗留任务。需要显式控制 tick 的测试在启动 TestClient 前替换 `main.dispatch_forever`，保留严格队列断言；恢复/后台调度仍由其他合同测试覆盖。
- Playwright `goto` 完成不代表 React effect 已注册。全局键盘快捷键测试先等客户端 effect 后才出现的 `.user-chip`，再发键；不加 sleep、不重复发键或放宽可见性断言。

## 2026-10-30 P6 无错误陈述门槛

- **23 项的单次运行，波动约 ±4 项。** v6 和 v7 只差一条提示规则，严格项却从 20 降到 16，新增的失败都与改动无关。所以单轮结果不能证明某条规则有效或有害。要看错误陈述总数这类跨轮趋势；要判断一条规则是否有效，同一份提示至少跑两次。
- v7发现checker命名空间豁免漏了HTML `xmlns`，不要把标准SVG URI当联网；保留原check，复核只清除这条误报并记录差异。`items.cost / items.cap`数组算术会求值成0；名为top5的变量不等于截取了前5，必须核对求值行数。见[迭代v7](docs/progress/updates/2026-10-30-genui-real-model-eval.md#迭代-v7)。

- 通用数据核对提示词也要逐条比较no_ui，不以“没拒答/没过度保留”推定无副作用；生成物的合计、主题颜色、播放状态等说明必须与实际代码对齐。v6暴露“说有合计但只渲染表”和fallback颜色不符，见[迭代v6](docs/progress/updates/2026-10-30-genui-real-model-eval.md#迭代-v6)。

- P6已由用户改为“整份回答没有错误陈述”；正确手写数字、块外复述/排名及表达式来源仅诊断。不要再把`conclusion_compliant=false`或`expression_provenance=false`直接汇总成strict失败；也不能只看数值，必须读hint/Callout/HTML fallback。明确标记的推测与调查建议不冒充事实错误，缺语义审核不可计通过。
- 旧`v5-check.jsonl`有历史root/引号/sum兼容性判定；本次离线重判固定使用43条`v5-recheck.jsonl`与26条`v5-retry-check.jsonl`，避免把运行时兼容性变化当成模型采样变化。原始答案保留在`/tmp/ailab-genui-eval/`，不要清理。计数和引文只维护在[评测记录](docs/progress/updates/2026-10-30-genui-real-model-eval.md#新门槛重判)及两份judgments。

## 2026-10-30 GenUI 目录 v2 与按需披露

- **OpenUI Lang 只有位置参数。** `Metric("营收", 5, unit: "亿")` 这类命名参数会让整句解析失败。v2 把可选项放进末尾的 `opts` 对象字面量：对象里写错的键会被单独丢弃，其余数据不会错位。新组件的主数据不超过 4 个位置参数，其余一律放 opts。
- **按需披露只能延后“怎么写”，不能延后“什么算对”。** 第五轮把“结论只出现在界面里”“不做未要求的统计”从系统提示移进了工具返回的指南，严格一致性从 11/23 掉到 5/23，失败几乎都是在块外写数字或比较。决定回答对错的规则留在索引（系统提示）里；指南只放语法、内置函数和组件用法。
- **模型会在中文字符串里用 ASCII 引号引词。** 例如 `Callout("更像"积压订单"")`。词法器会在第二个引号处截断字符串，后面的内容溢出成多余参数被丢弃。`toProgram` 的规则是：引号后面紧跟运算符、右括号、逗号、冒号或行尾时才算字符串结束，否则当作文字转义。代码字符串里的 `\"` 仍要照写，否则 `print("hi")` 中的 `")` 会被当成结束。
- **字面量形状错误在解析时就被丢弃，不会走到 Malformed。** 例如 `Timeline("x")`。要测试“数据格式有误”这条路径，得用 `@Each` 之类的表达式在运行时造出错误形状，见 `tests/genui-components.test.tsx`。
- **CSS 自定义属性会继承。** Grid 的 `--cols: 3` 曾渗进卡片里的 KeyValue，使字段竖着挤成一字一行。凡是用 `var(--x)` 排版的元素，都要在自身规则里声明默认值，不能依赖 `var(--x, 默认)` 的回退。
- **`textWidth` 估算的中文宽度偏窄。** SVG 标签列在估算值上要多留 16px，否则 Sankey 的“注册”会被截成“注…”。
- **贝塞尔曲线不会经过控制点。** Flow 的回边曾用三次曲线绕行，结果仍穿过同层的其它节点。现在按层间空隙走正交折线（`route`），绕到所跨各层全部节点之外。
- **指南每个 Run 只发一次。** 标记 `genui_guide_sent` 存在 Run 状态里，上下文压缩时清除。以后新增任何会丢弃历史工具结果的路径，都要同时清除这个标记，否则模型手里没有语法说明。
- **HtmlArtifact 的规则只写在它的组件文档里。** 关闭页面功能时，Agent 会删掉索引和指南中提到 HtmlArtifact 的行；指南里一旦出现这个词，删行后上下文就不完整了。契约测试会检查这一点。
- **组件可视检查的做法**：临时建一个页面渲染 `<Answer text=…/>`，覆盖全部组件，然后用 `next start` 起服务。Playwright 带任意值的 `ailab_token` cookie 访问（proxy 只检查 cookie 是否存在），分别截 1280/375 宽、浅色/深色。检查完删掉临时页面。

## 2026-10-30 P5 收尾：样式并层、CSP、HtmlArtifact 与 ui-state

- **去掉 legacy 层会改变胜负，不只是搬文件。**
  - 以前 components 层整体压过 legacy；同层后改由特异性和先后决定。
  - 三类回归都真实出现过：
    - 更具体的旧规则反超重建组件，例如 `button.side-link{font:inherit}` 盖掉 `.side-link` 的字重。
    - 旧文件拆成多个区域文件后先后颠倒，例如 `.search-dialog` 失去对 `.modal` 的覆盖。
    - 模板拼出的类名被当成死类删掉，例如 `tone-${kind}`。
  - 静态推断要么漏（`:last-child`、简写与全写属性），要么误报上万条。
  - 可靠的做法是同一份代码出两份构建：一份用迁移前的样式，一份用迁移后的。在同一 DOM 上逐元素比较计算样式，并强制 `:hover`/`:focus-visible`、打开菜单和弹窗。脚本思路见本轮更新记录，临时脚本不入库。
- **preflight 会去掉浏览器默认样式**，包括标题粗体与字号、段落外边距、列表标记、`svg` 的 block 显示、输入框边框。Portal 依赖的部分已在 `styles/base.css` 顶部补回。新增的元素不要假设有浏览器默认样式。
- **srcdoc 会继承父页 CSP。** 父页启用 nonce 后，srcdoc 里的内联脚本全部失效。生成页面和交付的 HTML 一律走 `app/frame/[kind]`，由它下发自己的 CSP，文档用 postMessage 投递。
- **`document.open/write/close` 会让 iframe 再触发一次 load。** 检测“页面跳走”时，run/doc 类型要预期 2 次 load，只有第 3 次才算离开；preview 用 innerHTML，只有 1 次。
- **父页 `frame-src 'self' blob:` 会拦住 iframe 跳到外部地址**，Chrome 显示错误页并触发 load。宿主据此停止页面，不需要 `allow-top-navigation` 之类的放宽。
- **FastAPI 路由里做同步 DB 提交必须写成 `def`**（线程池执行）。写成 `async def` 会阻塞事件循环（ui-state PUT 曾经如此）。
- **离线夹具**：
  - `[[genui-html]]`：可运行的抛硬币页，会探测 fetch 和 localStorage，并写 `body[data-ran]`。
  - `[[genui-html-escape]]`：300ms 后尝试跳转。
  - `[[genui-html-cut]]`：围栏不闭合。
  - `[[genui-interactive]]`：滑块会触发 ui-state 保存，E2E 等待 PUT 返回 204。
- **不要用 `git rm --cached` 或任何会改动暂存区的命令整理文件。** 曾经出现过一条来源不明的 `D app/globals.css` 暂存，已用 `git restore --staged` 恢复。删除文件就直接删工作区文件。
- **视觉对比用的服务栈要作为后台任务常驻运行。** 普通 shell 退出时会回收它启动的 uvicorn 子进程，Portal 随后会一直卡在登录。
- **Chrome 里 `white-space` 是简写**（`white-space-collapse` + `text-wrap-mode`）。后面的 `text-wrap: balance` 会重置 text-wrap-mode，迁移前写了 `nowrap` 的标题其实一直会换行。比较“迁移前后”要比实际计算值，不要比声明。
- **grid 的自动轨道按子项的 min-content 定宽。** `.modal{width:100%}` 放进 `display:grid` 的遮罩后，一个不换行的长标题会把对话框撑出手机屏幕。遮罩上写 `grid-template-columns: minmax(0, 1fr)`，轨道等于内边距内的空间，不要在每个弹窗上补 max-width。
- **OpenUI 解析出的树里，表达式还是 AST**（`@Each`、`@Sum`、`$变量`）。任何读组件参数的地方（复制、打印纯文本）都要先 `evaluateRoot`，按 Renderer 的方式给 store 播种：`$` 键交给 `initialize` 作 persisted，其它键用 `set`。求值后控件的 value 仍是 `{__reactive, target}` 绑定，要用 store 读当前值。直接读解析树曾让表达式算出的表格在复制时抛 `rows.slice is not a function`。
- **语法合法的程序也会算出错误的形状**：`rows + [total]` 得到数字 0，一行里可以嵌套多行。Table 和图表收到的数据用 `tableRows` 和 `chartData`（`catalog/schema.ts`）检查，形状不对就显示“数据格式有误，无法显示”，复制时省略该部分。书写中不提示，因为值可能暂时缺失。不要过滤成空表或错位的图。
- **Portal 启用 CSP 后，Playwright 的字符串谓词会被拦截。** `wait_for_function("window.x === true")` 这种裸表达式在页面里走 eval，会报 `EvalError … 'unsafe-eval'`。E2E 一律写成函数形式：`"() => window.x === true"`，或 `"(n) => …"` 加 `arg=`。不要为了测试给 CSP 加 `unsafe-eval` 或 `bypass_csp`。
- **性能代理“历史轮次 mutations”要分来源看。** 流式期间历史轮次的变化全部发生在 `.turn` 元素本身（`childList`、`style`、`data-mounted`），是有界渲染把滚出视口的轮次换成占位，不是内容重渲染。`probe.js` 按记录目标区分：内容变化预算为 0；挂载/卸载和最后一个 delta 之后的完成态变化只作信息列出。

## 2026-10-30 前端重设计与可交互 GenUI

- GenUI“不能用鼠标交互”不是 OpenUI 的限制：之前提示词 `bindings:false, builtinFunctions:false`、目录只有 Button/Tabs、图表没有指针处理。OpenUI 本身支持 `$变量`、reactive prop、表达式与内置函数。控件 prop 要在 zod schema 上 `markReactive`，渲染侧用 `useStateField(name, value)`；只改 TS 不重新 `npm run genui:prompt` 会让模型看不到新语法。
- Tailwind v4 `text-[var(--text-sm)]` 被当作颜色，不设字号，是之前字号混乱的根因之一；字号只用 `--text-*` token（CSS 中）或 `text-(length:--text-sm)`。
- SVG 图表不要靠 viewBox 随列宽缩放：文字会跟着变大变小。用 ResizeObserver 量宽度后按像素绘制，轴文字固定 12px。
- 全局元素规则（`a{color}`、`button{min-height;padding}`）会压坏具体控件（侧栏标题、`.switch`、勾选按钮）。全局元素规则只放 font/radius，尺寸交给具体类。
- 文字溢出修法：flex/grid 子项 `min-width:0` + `overflow-wrap:anywhere`；表格数字列 `nowrap`、表格横向滚动；GenUI 网格列按容器查询（`.genui-block` 是 container）而不是视口；超 8 位的 Metric 用中文紧凑记数，原值在 title。不要用 overflow:hidden 遮。
- Mock `[[genui-*]]` 夹具会被自动标题请求复用：`_genui_answer` 对 `{"question":` 开头的标题提示返回 None，否则会话标题是 openui 源码。
- Playwright 点 dev 服务器页面会被 `nextjs-portal` 遮罩拦截，dev 截图脚本加 init 样式隐藏；E2E 用生产构建不受影响。
- 对话开始后模式固定（API 返回 409 `conversation_mode_fixed`），Composer 不再显示模式切换；改模式只能“从这里分支为对话/工作”。技能只在可用 Work 时出现在 + 菜单。

## 2026-10-29 Portal 前端栈与 GenUI 集成

- 用户随后明确要求所有现有模型默认启用结构化GenUI：Settings默认true，随仓库与本机模型目录均包含genui。提示词准入仍同时检查全局开关和模型capability；部署渲染代码却保留两处关闭，会让用户只得到普通HTML代码块。不要再沿用旧“默认关闭/P6前不启用”结论；自由HTML执行仍未实现。
- Composer 内菜单不能放在带 overflow-x 的滚动工具栏中，否则纵向弹出也会被裁掉；停靠区需有正确层叠，窄屏模式控件不可收缩。删旧 CSS 时检查孤立选择器，曾残留 `.shell.collapsed` 使下一条项目首页规则只在侧栏收起时生效。
- 平滑文字要按固定起止时间释放并在追上后停 rAF；按“剩余量×dt”指数递减既不保证120ms完成又会空转。GenUI复制应点击时转换，首次 render 早于懒加载完成，不能提前固定成源码。
- 围栏感知历史裁剪只禁止切开 UI；普通 Markdown 超限仍保留前缀，不能因整个 md part 大于预算而返回空历史。
- 非阻塞 outbox 需处理同毫秒排序、首页准备中后续消息归并及上传对象释放；队列持久去重已补齐（0038），边界见下方“首次预览与发送幂等”。验收/未达标预算见[集成记录](docs/progress/updates/2026-10-29-frontend-genui-integration.md)。
- OpenUI `library.prompt()` 的 inlineMode 要求 `openui-lang` 围栏和“只输出变化语句”，与本项目协议冲突；其 Action 段固定宣传 @OpenUrl 并带示例。`catalog/prompt.ts` 去掉这两段换成自己的；改目录后 `npm run genui:prompt`，漂移测试逐字节比对。
- `@openuidev/*` 在 Node 端（生成提示词时）默认向 PostHog 上报，脚本已带 `OPENUI_TELEMETRY_DISABLED=1`；不要在别处直接 `node` 调用目录。
- 围栏拆分以 Python 与共享夹具为准：原文逐字保留（CRLF、空白 md 段、UI 末尾换行），只认反引号 + 小写 `openui`。两侧都只按 `\n` 分行（Python `splitlines` 会在 U+2028 等处断行，JS 不会）。
- OpenUI 词法器会无报错地吃掉两类模型自然写法，任何解析 OpenUI 的地方（渲染、复制、评测 `evals/genui/check.ts`）都先过 `catalog/program.ts` 的 `toProgram`：① 名字只认 `[A-Za-z_][A-Za-z0-9_]*`，`表 = Table(...)` 的中文名被跳过，整块渲染为空（对话模式“可视化没出来”的根因）；② 单引号字符串解码 `\"`→`"`、`\n`→换行、`\d`→`d`，HtmlArtifact 页面里的 `html+="<div class=\"row\">"` 变成坏 JS（`Unexpected identifier 'row'`）。`toProgram` 把单引号串当原文，只有 `\'` 是转义；双引号串仍是 JSON。提示词同步要求反斜杠只写一个，不要再让模型手工双重转义。
- 工具流量不回放进后续轮次的历史：若历史里没有上一轮读过的来源，模型会否认自己引用过的数字。`history_sources` 把来源标题和 URL 附在历史回复后。
- `capability_discovery` 只是把未用工具的 schema 挤出提示词，不是权限：模式允许的工具按名字直接调用（search 后 read_url）要执行并激活，不能报 `not available in this turn`。
- 滚动跟随统一用 `lib/use-follow.ts`：ResizeObserver 驱动、指数缓动、远距离直接跳；用 `placed` 区分自己写的 scrollTop 和读者滚动，否则缓动本身会被当成“用户上滚”而停止跟随。
- `npm test` 是 Vitest：旧 `node:test` 用例经 `tests/node-test-compat.mjs` 提供 `t.mock/t.after`；React.lazy 会缓存失败的 import，重试必须新建 lazy。
- 旧 `.chat-main{padding-top:48px}` 已删：浮动按钮不占行，靠 `.messages-inner` 顶部留白；E2E 依赖的类名/中文标签见 `platform/portal/AGENTS.md`。
- 懒加载会被常量导入悄悄打破：从 `catalog/schema.ts` 取一个数字就会把 zod + lang-core（约 65 KB gzip）拉进对话页首包。聊天侧只从 `catalog/limits.ts` 取常量；验收看 `.next/server/app/(app)/c/[id]/page_client-reference-manifest.js` 的 entryJSFiles 不含 `createLibrary`。
- E2E 只复制 conftest 列出的 Portal 目录/文件；新增顶层目录或配置（如 `styles/`、`postcss.config.mjs`）要同步加入，否则构建失败或 Tailwind 静默无输出。
- 发送不再等附件：E2E 不能再断言“上传中发送按钮禁用”，改断言待发送消息出现且附件就绪前无会话/消息 POST。队列 POST 也带 `client_send_id`；不能仅在 pending 的 ≤5 条中去重，须按用户查询持久列。

## 2026-10-29 首次预览与发送幂等

- 2026-10-30 修复：队列0038持久列/用户唯一索引及请求摘要跨 queued/started/steered/canceled 重放，均返回201；不同请求/会话或与直接发送跨入口复用ID返回409。直接入口也须检查队列，派发自身不携带ID创建Run。fork仅复制历史，不复制发送ID；取消保留身份，reaper随已删除会话清理。旧JSON不回填（可能重复且无原始请求摘要）；修复仅保证新入库身份。验证见[记录](docs/progress/updates/2026-10-30-queue-send-idempotency.md)。

- 文件库 FilePreview 与 Run 工作区预览是两个合同：只有后者已有 kind=pdf；不要只改 Broker 输出 PDF，API 完成校验仍会拒绝。
- ready 后预渲染只在原事务写 outbox，不同步调沙箱；20MiB 默认阈值、原渲染槽和 pending/终态幂等必须一起保留。产物创建时 ORM 默认 purpose 尚可能未落地，需 flush 后判断 library。
- OCR 选页不能当全文预览缓存；仅复用页数已知且完整覆盖前 min(40,总页数)、资产属于同 file 的清单。
- client_send_id 按用户唯一，重试须先于附件/额度/会话忙碌检查；参数化测试共用数据库，同用户不同发送必须使用不同 ID。Docker 默认 socket 不存在时复用个人 shim；Mac 未共享路径的 bind 会在 VM 变空目录，基准脚本/合成文件改用 stdin 注入。
- 新worktree跑文档/OCR测试前先在vendor/zcode-office-runtime执行npm ci --ignore-scripts与npm run build；缺runner.mjs会让整组原生文档索引/覆盖测试失败，不能误判为业务逻辑回归。

## 2026-10-29 GenUI 历史与摘要

- UI 围栏必须在 SQL substr、JSON 序列化和压缩分片之前处理；只替换 Python 的字符切片仍会保存半截源码。历史保留的是整条祖先链的最近一轮，不是每页最后一轮；摘要输入须独立转占位，不能复用最近轮源码。共享 CRLF 夹具按 bytes 解码读取，避免 universal-newline 改写合同。

## 2026-10-29 办公视觉覆盖与文件等待

- 原生文字质量须来自结构化事实，不能把页标题、empty/image占位当正文；新parser ailab2，旧PDF占位只做版本受限兼容。OCR缓存按文件内容/引擎独立于原生parser升级复制，旧技能版本/付费pending工具须可恢复。
- OCR只读字，不能推导图片、架构图和布局语义；Chat视觉页面准备必须在模型调用前完成，Work返回视觉覆盖/截断。含图Excel应将单元格读取与绘图互操作隔离，原文件保持完整供视觉渲染。
- 自动文件等待复用interrupted+file_dependency，但SSE和Portal必须识别自动继续，不能终止流、允许新发送或要求手动继续；写等待事件必须先于结束attempt。恢复仍须重查额度/并发/来源/终态。PG不能DISTINCT含JSON的整行，用file引用半连接。
- 详见[ADR 0026](docs/architecture/adr/0026-document-visual-coverage-and-file-waits.md)与[验证记录](docs/progress/updates/2026-10-29-document-reading-reliability.md)。

## 2026-10-29 E2E 保存完成与登录重写

- “保存”按钮开始请求即改为“保存中…”，精确“保存”消失不能证明落库成功；刷新/后续操作前须等两种状态的按钮都消失，失败仍须保留断言。#102 的记忆暂停 E2E 因此提前刷新。
- Next 16 默认将 loopback URL 规范化为 localhost，`NextResponse.rewrite(new URL('/login', req.url))` 在监听 127.0.0.1 时可能被 router 判成外部代理，#103 曾出现 socket hang up/HTTP 500。保留 `skipProxyUrlNormalize: true`，用实际 NextRequest 与 router 的 origin 比较回归锁定内部重写；不能靠登录重试掩盖问题。见[修复与验证](docs/progress/updates/2026-10-29-main-ci-fix.md)。
- macOS 后台进程回收测试的 `killpg(pid, 0)` 曾短暂返回 EPERM，单独重跑通过。测试在原截止内继续探测，仍只接受 ESRCH/ProcessLookupError 证明进程组消失；不能将 EPERM 当成功，也不能放宽 Linux 的回收要求。

## 2026-10-29 测试部署与历史备份

- 测试环境仅明确要求才部署、备份或执行部署后验收；具体规则以 AGENTS.md 的用户测试环境约定为准。不要重复 PR 已做的检查或默认全量重建 Chromium/OCR。
- main@5eef026 已重新部署，后续功能验收按用户要求取消；历史备份及内盘副本已删除，下方旧恢复路径不再有效。运行数据和配置保留，个人细节仍只在 AILab-local。见[部署与清理记录](docs/progress/updates/2026-10-29-main-redeploy-cleanup.md)。

## 2026-10-29 fork必须迁移程序状态

- fork不能只复制正文和input_file_ids。必须迁移已确认插话及其AppliedMessagePosition/MessageFileRef、agent_read依赖、顺序/来源快照及invalidated_epoch；合并runtime_meta不能抹掉source_invalidated。历史model-context也须包含插话文件句柄。
- 结构化摘要内的run_id随分支映射，frame/source表和待校准offset另存新身份；再次压缩只能引用本分支祖先。异常来源从保留原文重建。不是复制原frame_id或仅替换摘要边界。
- 复制轮次保留历史mode，分支初始mode取forked_from.mode；有新轮次后按最新自有轮次恢复。详情白名单、来源失效序列化和清理必须保留forked_from_run_id，否则前端刷新后会误判。用户主动切换后轮询不覆盖。技能强制Work时附件/模型/模板准入也要使用有效mode。
- 隔离旧源码做红回归时pytest默认prepend会先加载现工作树，须固定importlib和完整资源路径；不要把错误导入后的全绿当作证明。手机测试从桌面登录后切窄视口要重载以符合首次手机打开状态，不能force点击穿透侧栏。
- 本轮验证/部署和边界见[集中审查](docs/progress/updates/2026-10-29-fork-context-review.md)。此前已真正丢失关系的旧分支不凭空修复，重新fork原会话；个人部署记录只在AILab-local。


## 2026-10-29 模型窗口85%与输出预算

- 用户已选完整catalog上下文85%触发，允许动态缩减本次输出；不要再按70%输入预算或Work65%计算。Chat/Work历史和Work执行共用ModelPolicy阈值，API显示同源；已有显式管理员设置需通过API变更，本机已开启/0.85。
- 去掉按“模型最大输出”固定预留后，必须同时按剩余窗口计算ModelCall输出、reservation和provider max_tokens。摘要打包仍须为实际摘要输出留空间，不能挤到只剩1token。
- 阈值按校准估算与配置窗口计算，不冒称供应商满窗容量已验；不自动压缩新附件冒充全文。旧checkpoint非收敛比较要包含threshold，模型/阈值变化不沿用旧65%结论。见[记录](docs/progress/updates/2026-10-29-context-85.md)。

## 2026-10-29 网页403要区分读取与预览

- HTTP403是上游拒绝，不能和non-public/DNS混为一谈；同URL分别核实静态抓取和隔离浏览器。模型read_url已有恢复不代表Portal iframe也接入了；`reader/render`现在只对可恢复失败调用有界HTML快照。
- iframe错误返回可读HTML并保持失败状态；网站仍拒绝时不能以浏览器success或200验证码页冒充文章成功。示例：力扣浏览器可读，而两个知乎样本仍403。
- API调用crawler需独立配置crawler token、网络入口来源和secure校验；不可为预览开放浏览器直连网络或给crawler业务身份。原脚本禁用CSP保留；见[记录](docs/progress/updates/2026-10-29-reader-preview-403.md)。

## 2026-10-29 对话附件必须走侧栏标签

Turn里的用户附件不能直接调用useFileViewer全屏shell；由Chat用file_id建立标签并交给PreviewPane，复用FilePreviewBody。文件名只能显示，不能作为上传附件唯一身份。原生提取、点击预览、OCR和模型输入分别验收；旧DOC/PPT/XLS可预览不等于全文已入索引。浏览器截图必须等待图片decode并禁用动画，避免把白页占位/动画透明阶段当实际显示证据。见[本轮记录](docs/progress/updates/2026-10-29-chat-attachment-tabs.md)。

## 2026-10-29 VPN fake-IP与本机网页读取

- 全域refusing non-public且DNS落198.18.*时先查宿主VPN→Colima→容器解析，不要直接放行fake-IP或禁用SSRF。公网检查旧版与新版一致。当前本机相关服务已指定真实DNS，详见[记录](docs/progress/updates/2026-10-29-reader-dns-local-fix.md)。
- reader-egress有namespace nft白名单，Docker自定义DNS在该namespace转发也需要精确放行指定服务器UDP/TCP53；不能只改compose dns后忽略出口规则。Mac VPN/全局DNS不需修改，用户系统与AILab执行环境分开配置。


## 2026-10-29 本机体验部署后的边界

- 用户已明确授权当前文件/上下文改造部署，覆盖下方历史“不部署”限制；不push/PR/merge。实际dirty快照与必要验收见[本机部署](docs/progress/updates/2026-10-29-file-context-local-deployment.md)。个人部署仍只从AILab-local运行，不从repo同名bin调用。
- 当前本机DB0036，旧API0033镜像不能直接连新库回滚；保留新写入，完整迁移前备份恢复到新空volume后再切旧镜像。不要覆盖用户验收后新增数据。个人备份/凭据与原始浏览器证据留本机，不复制进Git。
- CPU OCR已显式部署到独立内网容器用于本机体验，不是CANN成功或产品默认变更。冷模型权重预置且hash验证，API/Worker无OCR解析器。资料库根视图不显示项目文件，网页验收要进入正确项目文件夹，别把分类选错当文件消失。


## 2026-10-29 核心实测后续规则

- 文件/Office/OCR任务在项目内`.worktrees/file-context-ocr`、`feat/file-context-office-runtime`，证据目录统一`docs/reviews/file-context-2026-10-29`；不要再放回旧Memory命名临时目录。主目录另有视觉设计脏修改，不能覆盖。
- DeepSeek已配置，缺真实模型条件的旧结论已被覆盖；本机凭据仍按个人AILab-local交接从原文件读取，不复制进仓库。有限合成实测不能冒充500轮10代保真。
- JSON摘要必须提供实际schema并计预算。next_actions/uncertainties是字符串数组，不能只说“四组条目”让模型猜类型。压缩要同时带程序确认的交付状态，避免旧摘要/pending输出误写未交付；恢复仍只认程序账本。
- 校准只确认实际发送片段的evidence_id前缀，待处理handle/offset跨frame保存。OCR副本是新file私有对象，seconds=0/copied_pages表示缓存，不能算新推理。恢复墓碑通过ops内部API应用，脚本不写业务DB，永久删除保持410合同。
- 实际CPU OCR清晰中文单图约1.03秒，但warm RSS快照约686MiB；权重大小不代表进程内存，保持独立执行器。NPU缺provider须明确失败，不能把CPU实测写成CANN验收。


## 2026-10-29 文件实现与集中验收陷阱

- 当前用户已授权文件方案实施，覆盖下节“只设计”历史限制；不push/PR/merge/部署。Office Runtime平台持有，在Broker系统沙箱执行，禁止装进API/Worker业务进程或用户可切换skill目录。OCR只计量、不计费，不设累计用户用量上限；资源槽位仍须有界。
- 上游pagination会截掉超大单元中部，宽表默认列数也有限。要核对连续cursor及全部行列，不能只测小fixture。固定源码hash与本地patch列表分开；同范围冷/热/缓存性能要分别测，现有小样本Office冷启动/RSS高于旧Python。
- 每次解析输出使用file/job独立命名空间，Worker尝试claim和API提交二次检查；不同进程加载旧路径协议会产生假失败。最终组测必须冻结代码并重启五服务。Mac tar设置COPYFILE_DISABLE=1，拒绝AppleDouble/软链，不放宽归档校验。
- macOS mock沙箱测试需GNU stat；使用coreutils/findutils路径。队列promotion在autoflush=False下必须flush新增消息位置。发现工具会增加模型轮次，测试释放信号不能在能力搜索后再次clear造成伪超时。
- OCR全量页缓存放按file/page/fingerprint索引表，File.meta只留最近页状态；不能把最近20页当全文缓存。删除在线epoch栅栏必须贯通原文、dream、向量回填和回放，后台清理还包括候选、undo快照和副本。

## 2026-10-29 文件与多代压缩的设计陷阱（历史设计阶段）

- 本次办公skills是平台内部能力，用户不可管理，不得注册到用户可切换技能目录后只藏UI。优先按[复用plan](docs/architecture/design/file-context-ocr/09-upstream-reuse.md)接ZCode Office Runtime/viewer；上游分页不等于流式解析，必须同范围比较冷/热耗时和RSS。ZCode部分生成skill frontmatter与同目录LICENSE有冲突，runtime/viewer和MiniMax许可分别记录，不笼统套仓库根许可。用户明确只改plan，不能从“建议直接用、弄过来”推断本轮实施授权。

- 用户的5张图仅指输入框每轮附加独立图片；后续轮次可继续，不得扩大为模型请求/历史/文档页的5张上限。“不默认整份渲染后一次回灌”不等于禁止全篇分批审阅或全篇逐页转换缓存，处理范围与模型输入范围必须分开。

- 新产品分工以[模块总览](docs/architecture/design/file-context-ocr/README.md)为准：Chat预处理/超限准入，Work自主按需；RapidOCR＋昇腾NPU优先。上游CANN不可用会回落CPU，required-backend/readiness和真实节点执行证据缺一不可。冻结架构已有OCR卡位但原模型不同，不能自动重分卡。
- skills正文按需加载不等于tool-search：当前整批schema与目录仍进prompt。消息附件顺序按用户提交ordinal，不按上传完成时间；自然语言歧义要返回候选。普通删除是回收站，彻底删目录也不证明DB索引/摘要/工作区副本已清。

- 历史摘要已经会合并旧摘要与新增轮次，不要为此再造一套摘要。但`model_context`当前不传输入附件清单；文件在存储中不等于模型能识别来源/版本并重读。检查跨轮、checkpoint、工具读取三条路径，不只看本轮图片可见。
- 本轮整理排除image_url、保留原始输入，二次整理能包含旧摘要；头尾截取不能当连续消费。后续实现必须只推进真正被处理的part/event游标，配对工具与非重放围栏由程序保存，不靠摘要复述。
- CPU OCR模型权重小不代表运行时RSS低；Paddle官方轻量CPU benchmark仍有约2GB主存及多核开销。不得塞进256–384MiB Worker或把官方速度套到2vCPU；独立执行器同机/远端以实测准入。
- 本轮是[设计讨论稿](docs/architecture/design/2026-10-29-file-context-ocr.md)，不是已实现合同。原文件版本、类型化图片输入、来源删除、全文覆盖与费用必须整体验收；URL字符串不等于看过图片，摘要不等于原文。

## 2026-10-28 记忆完善后的合同

- 模型视图统一在`memory_recall/policy.py`，管理页完整文档不可直接进入模型。项目作用域、suppressed/invalid/review、读写策略贯穿bootstrap、工具返回/重放、历史原文、dream及checkpoint续跑；更新别只修一个入口。
- 0034的MemoryWorkItem保存真实段落/候选进度；不要重新换回created_at游标。原文必须包含已确认插话，未抽取段落/未应用候选留待下批，替换与全部失效是一组。晚到旧事实不能覆盖较新的已确认状态。
- 数据库事务/用户锁都不能跨模型await；输入固化及提交需要用户锁，网络在中间。embedding跳过条目时也结束事务，避免把PG旧用户行锁带到下一用户。每次实际后台模型调用单独reserve/settle，离线评估也必须有计费身份。
- 删除覆盖当前事实、episode/vector、候选、快照/plan、工具重放和词法缓存；保存事实正文仍按ADR0019独立删除，原话副本不享此例外。撤销必须匹配整理后的revision，不能覆盖之后的编辑。
- 查询计划测试使用独立数据库、4万行多用户和真实优化器；PG可合理选择旧user索引/增量排序，不能强绑一个索引名或关闭seqscan。手机新增控件后实测textarea宽度，flex不溢出仍可能把中文挤成竖列。
- 实现/原始红探针/最终验证与未验边界统一见[本轮报告](docs/reviews/2026-10-28-memory-system-hardening.md)。下节是修复前审查，不能再当作当前未修复状态。

## 2026-10-28 记忆审查：边界必须贯通

- bootstrap过滤正常不代表工具/dream也正确；管理页完整视图不能直接作为模型工具结果。suppressed、invalid、enabled与auto_save必须在所有模型输入/自动写入路径和提交边界执行。`main@0659101`仍有已复现缺陷，尚未修复；详见[本轮审查](docs/reviews/2026-10-28-memory-architecture-review.md)。
- 输入预算裁剪不等于处理完成；推进到取回的最后一轮会永久漏掉未送模型的历史。事实替换及对应失效须作为一组计预算，不能按操作数切断；created_at游标还要覆盖晚完成及同时间戳。
- 删除谱系要覆盖当前文档、episode/vector、整理快照/计划及撤销；保留保存事实的产品决定不等于允许保留已承诺清除的原话副本。引文必须匹配其声明的来源，不能只验证“本批中出现过”。
- SQLite写入后不要跨LLM await保持未提交事务；实际探针确认会锁住其他账号写入。先短事务提交输入，再事务外调用模型，最后重检版本/墓碑/资格提交。审查的12失败探针是缺陷证据，不是产品回归通过或已修复。

## 2026-10-26 RFI正文与标签首字

- 噪音类名是容器启发式，不能据html/body上的全站布局类（RFI `u-header-sticky`、`u-header-with-tag-list`）丢弃整份文档；仍保留hidden/ARIA等显式隐藏规则以及header/nav/footer子树过滤。真实页面服务端已有正文，先比较原HTML与提取输出，不能把空提取一概归为脚本渲染。截图对应页修复前0块，修复后23块、12段主要正文全部保留。
- 全局button是inline-flex且justify-content:center，长标签文字作为匿名flex项溢出时会裁掉标题开头，text-overflow单独存在并不能修复。`.pane-tab-name`用block/left对齐，尾部ellipsis；选中标签滚动时按整个tab的起点定位，重置名称内部scrollLeft。用Range测量首字是否实际在按钮及tab条可视范围内，不能只断言DOM文字或title属性。

## 2026-10-26 手机侧栏历史必须在可交互前安装

- PR83在手机快速重开预览并返回时退到about:blank，本机同用例可复现；根因为setTimeout延后pushState，不能改测试加sleep掩盖。用layout effect在绘制前同步安装历史；Strict Mode重放复用当前标记，清理回退放microtask并检查effect generation，避免重放触发额外back。桌面/手机既有预览返回用例和网页来源合并回归6项通过。CI总门禁正确阻止失败，非CI平台故障。

## 2026-10-26 网页来源 tabs 与提取

- 网页预览须按 URL 保存独立标签，不能只用一个 `paneReader` 覆盖；标签名称使用来源标题，普通 Markdown 链接也要传入可见文字。最近4个文档保留原版/正文 DOM，较早标签保留但内容按需重载；不要无限保留第三方 iframe。关闭最后一个文件时即使网页仍打开，也不能再访问空文件数组的末项；关闭非当前标签不能改变当前阅读内容。
- 原版是保留 HTML/CSS 的静态预览，禁用站点脚本/子框架/表单与 meta refresh，防止 hydration、自跳转或重复执行。HTML 的 base URL 必须转义、移除页面原有 base，且保留 doctype 在首位，避免触发 quirks mode；批量删标签用片段一次拼接，不逐次复制全文。CSP/referrer-policy 必须通过 Portal 响应头白名单，否则 API 测试通过也不代表浏览器执行了策略。依赖 JS/登录/反爬的站点仍需外部原文入口，不声称完整浏览器镜像。
- 正文提取不能把文章自己的 header 与全站页眉混为一谈；布尔 hidden 是属性存在即生效（HTMLParser 的值常为 None）。无匹配开始标签的结束标签应忽略，不能清空解析栈。输出单块也受总字符上限约束，提取限额触发时向 UI 传 truncated；表格行合并必须退还已计字符。区域评分用前缀和，不反复扫描嵌套区域。
- 全局 `button:hover` 会改变模式按钮文字颜色，模式 hover 必须同时指定前景/背景；用亮暗主题至少4.5对比度验证。桌面直接 resize 到手机时可能保留展开的全局导航，测试应先收起导航或重新进入手机上下文，避免把导航遮挡误报为网页 tab 点击失效。


## 2026-10-26 会话写入与旧详情响应

- E2E 超时先看各 step 时长：PR93第二次并非测试挂死，Azure apt HTTP镜像25.9MB传输耗10m49s（40KB/s），仅剩不到3分钟跑套件。CI对该镜像替换为Ubuntu官方HTTPS源，不应加测试skip或放宽15分钟deadline。
- 请求内的读取序号只能避免“旧读覆盖新读”，不能避免“旧读覆盖确认写入”。PR93 CI 中 queue POST 201 与旧空队列详情 GET 交错，导致队列刚出现就消失；本地确认发送/队列增删改/恢复要递增写版本。旧读发现版本变化后重新取快照，并保留流结束跟随下一轮的 continuation，不能只 abort 后把后续跟随一起丢掉。用持有真实旧响应直到写入已可见再放行的浏览器用例复现。
- 侧栏 GET /conversations 默认 project_id=None，只含无项目会话；不能从该列表推断当前项目会话归属或删除后是否仍存在。项目删除后使用一次有界存在性读取，晚回包不得改动已切换的路由。
- PPT 成功渲染也可能走实时预览侧栏；同表格一样验证唯一实际面板及可见、完成加载且 naturalWidth/Height>0 的图片或有非空白像素的 PDF canvas，不能只等 `.viewer img`。详见[记录](docs/progress/updates/2026-10-26-sidebar-project-menu.md)。

## 2026-10-26 表格预览双入口 CI

- 文件卡“预览”按实际Run预览清单打开实时侧栏或通用viewer，受渲染后端能力影响。本机只验viewer不能推断Linux CI也走viewer；PR78首轮表格已正常出现在右侧，但测试强等`.viewer`60秒失败。端到端应验证唯一实际预览面板、表格内容及关闭/重开/继续修改；确定性分支测试分别构造有/无实时预览清单，不能为迎合断言改变产品。

## 2026-10-26 完整思考与长内容

- 用户已取消回答下方详情入口（取代前一轮详情tab决定）；展开过程一次显示模型返回的全文。`Run.result.thinking.text` 是有界缓存，完整权威数据是 `agent.thinking` 事件，不应把缓存扩成巨型JSON带进每个会话列表请求。过程200事件分页必须有可见底部自动续取，不能停在第一页冒充全文；既有30天事件保留策略仍有效。
- CSS折叠不等于免渲染。折叠正文不挂载；长Markdown按边界拆块，屏幕外保留测量高度；消息外壳与编辑/展开状态保留，避免全文DOM随500轮累积。共享IntersectionObserver按滚动容器复用；事件流尾部更新不能克隆所有历史步骤，且不要让12个会话缓存各自偷偷保留所有加载过的页。
- 聊天延迟挂载须显式启用，打印/导出必须完整渲染，不能因屏幕外未挂载而漏页。超长块和大量消息验收要查首尾全文、实际滚动、挂载数和真实输入；`scroll-behavior:smooth`会让超长过程连续跳转迟迟追不到目标，程序定位用原生即时滚动。虚拟步骤需等待实际内容挂载再断言，不能把占位符当作步骤丢失。


## 2026-10-26 任务详情侧栏

- 详情是当前对话的附属视图，应通过同一预览侧栏 tabs 打开；独立 `/runs` 路由会卸载聊天，写死返回 `/` 则丢失上下文。不要用事件到达触发 `scrollIntoView`，历史回放会强制滚到结束；侧栏焦点切换用 `preventScroll`。桌面侧栏改变聊天换行，关闭后须在原宽度恢复打开前的滚动位置。
- 将链接改成按钮时检查手机 `nth-of-type` 样式：它按所有同标签兄弟计数，复制/重试等按钮会使“详情”意外命中隐藏规则。辅助元数据隐藏应限定 `span`。新增详情滚动区需覆盖通用 `.preview-body` 的 overflow，浏览器实际滚轮验证，不能仅检查 scrollHeight。
- 当前模型交付的无实时预览清单文件走文件卡“预览”打开 `.viewer`；有 `Run.result.previews` 的实时文件才走侧栏 tab。E2E不得假设产物自动打开，或把两种入口混为一个。验证见[本轮记录](docs/progress/updates/2026-10-26-run-details-tabs.md)。


## 2026-10-25 分支模式与手机预览

- 从纯Chat历史fork为Work时，旧Run.options仍为Chat；会话级forked_from.mode必须作为前后端分支能力推断的初始状态，否则早期轮次可绕回Chat。旧JSON无mode时仍按历史推断。
- 手机fixed chat-layout会形成独立层叠上下文，内部预览z-index不能压过外部main-top；预览打开时须处理外部浮动按钮，普通聊天也须预留顶部空间。collapsed选择器优先级可覆盖手机面包屑padding，需一起核对。

## 2026-10-25 本机部署恢复与长任务验收

- 外置存储掉线后，VM/SSH mux仍可存活并占用Portal入口端口，监听存在不等于页面健康。先核对实际挂载UUID、`COLIMA_HOME`、Docker socket与进程命令行；同名Colima profile可分别位于内盘/外盘，不能按名字停错测试VM。个人路径/端口/原始证据仅留本机独立HANDOFF。
- 已有自动挂载器可能为保护旧VM而拒绝重挂，不要直接mount或在线fsck绕过。按既有恢复工具先停该部署的遗留进程，确认退出后再重开原镜像、未挂载验证、挂回原位置；20秒退出截止失败不代表必须强杀或清库，先只读复查再决定重试。USB断链先于APFS错误支持链路/电源/控制器方向，不能因此宣称具体硬件已定位或磁盘损坏。
- 数据保留部署须先停写入者，备份完整业务卷（DB、WAL及对象/存储元数据），验证checksum并实际解包检查。迁移起点以真实DB为准：本次旧部署0028，实际走0028→0029→0030；切换前后比较原ID保留，允许验收/用户合法新增，不清空账户、配置或资料库。
- 提供商402余额不足与产品429日/月额度暂停不同；402不能写成额度暂停验收通过。费用按官方余额差与实际token记录，内部加权额度不等于人民币；只给专用验收账号配置独立保护，保留普通用户/默认策略。详见[部署验收](docs/progress/updates/2026-10-25-deploy-long-runs.md)。
- `events/history` 的事件内容字段是 `payload`，不是 `data`。Playwright从桌面改手机宽度会保留当前展开的侧栏，需关闭它或在手机宽度reload/新开上下文后再点来源；资料库手机端隐藏图标按钮 `.lib-open`，点击可见文件名。不要将这些探针错误计为产品失败，亦不能把底层sandbox探针计为真实模型长任务通过。

## 2026-10-25 长任务最终真实沙箱验收

- 删除同 Run 被替代的 workspace 前，同一 checkpoint 事务也必须更新仍指向该对象的 `result.workspace_*`。暂停结果会跨 resume 保留，FAILED 又合并部分 result；只改 runtime_meta 会被旧 result 遮蔽，造成下一轮误过期、restore 或 Work fork 指向已删对象。fork 必须复制为新 Run 自有对象；GC 继续保护当前非终态 runtime_meta 的精确 key。
- Docker 的 `/tmp` 是独立 tmpfs。helper 从 `/workspace` 直接 `Path.replace` 到 `/tmp` 在真实容器报 EXDEV；先验证内容，再在目标目录写临时文件并原子替换，最后删除 staging。宿主 mock 的同盘目录无法覆盖这个边界。
- subreaper 只忽略 SIGCHLD 能回收僵尸，却不能停止被 SIGKILL/OOM 监督进程留下的活后代。helper reaper 使用 SIGCHLD 默认处理；Linux `waitid(WNOWAIT)` 保留已退出 leader 的 PID，先杀该进程组，再 waitpid 回收；继续回收收养的孤儿，避免 PID 复用竞态。Docker expiry PID 1 仍使用 SIGCHLD=SIG_IGN。macOS 身份检查也须拒绝 ps 的 Z 状态，否则清理会把僵尸误判存活并报 EPERM。
- 专用 Colima 用 `--activate=false`，Docker 测试的 CLI shim 强制 `--context colima-ailab-verify`，SDK 单独指定同一测试 socket；共享目录用用户缓存目录，不用宿主 /tmp。OpenSandbox 1.1.1 在 macOS 需要 setsid；测试只在缓存目录提供包装器，配置独立 store.db、dns+nft、回环端口和至少100端口的分配范围，绝不沿用个人默认 store。共享宿主不要运行全局pgrep的osb_down.sh；按本轮pidfile、命令行与配置hash核对后仅停止自己的PID。新 runsc 已改为完整 gvisor tarball，按官方 SHA512 校验，不能只装旧单文件；guest 缺 bzip2 时在宿主解包后复制完整目录。详见[最终验收](docs/progress/updates/2026-10-25-long-runs-sources.md#final-real-sandbox-validation)。

## 2026-10-25 额度暂停

- 用户日/月额度的429发生在模型reserve之前，不是上游模型故障；只在reserve接缝识别 `detail.code=quota_exceeded`，转为独立BudgetPause子类。不要把所有429都当额度暂停，也不要让它触发重试、fallback或LLM失败计数。标题推理仍独立best-effort，不能因标题额度失败暂停已成功回答。
- 普通预算暂停会自动交付outputs leftovers；额度暂停必须跳过额外交付，仅保存checkpoint、workspace和已有预览/产物/计划/来源。API合并部分result时必须主动清掉旧pause；成功继续在同一事务清除，后续其他暂停即使省略result也清除。
- 手动queue/resume须透传准入429 detail；自动终态派发则在savepoint拒绝后保持队列暂停，不能把预期额度失败回滚为前一Run失败。
- 月额度重置可能晚于72小时中断取消与3天workspace TTL：额度暂停的waiting_since作为取消宽限期起点取max(now,resets_at)，GC按有界对象页通过Run主键批查runtime_meta，保留live Run引用的精确checkpoint快照。只保护当前快照，终态/替代快照恢复TTL；独立GC测试须给collector绑定自己的DB factory，不能查全局未初始化或其他测试库。详见[本轮记录](docs/progress/updates/2026-10-25-quota-pause.md)。

## 2026-10-25 Run 网页来源

- `agent.tool_result.sources` 是单次工具的事件数据，不能当作重开会话的持久结果；来源与工具完成记录同 checkpoint 保存，终态写 `run.result.sources`，会话详情还需放行该键。旧 checkpoint 缺键须默认空列表；每次工具最多10条、整轮 URL 去重并保留首次顺序最多50条。waiting_user、预算与租约中断暂停也须写 result.sources；API在用户锁内合并暂停及失败的部分result，避免续跑后崩溃抹掉已保存的预览/计划。续跑仍从checkpoint恢复。见[交付记录](docs/progress/updates/2026-10-25-run-sources.md)。

## 2026-10-25 真实沙箱验收

- OpenSandbox server 1.1.1示例的egress DNS-only模式不能拦字面IP，即使SDK请求default_action=deny。Docker侧车模式必须用dns+nft，并先用正常非root容器证明TCP/UDP/ICMP目标可达，再从沙箱测阻断；不要把仅DNS失败写成完整断网。
- `osb_up.sh`生成安全新配置、拒绝原DNS-only配置，Docker也须绑定已启动进程的配置hash。修改文件不等于运行策略已更新；先停原server再显式更新，不能从/health=200推断正确进程。curl缺失在起进程前失败，回环探针绕过宿主代理。
- Docker自动到期会发生inspect→元数据exec竞态，以及rm撞自动删除；仅确认停止/不存在后收敛，不能吞daemon/权限错误。删除冲突等待有截止。
- Docker/OpenSandbox共享daemon时，metadata都包含owner标签；并行测试必须各用独立broker_owner，避免对账互相收养/清除。主线技能更新后先同步测试Office镜像，409 skill_index_mismatch不能靠放宽attestation解决。真实组合/失败历史/边界见[验收](docs/reviews/2026-10-25-real-sandbox-acceptance.md)。

## 2026-10-25 单机审查：恢复、快照与异步视图

- 性能门禁的p95按实测请求nearest-rank定义，保留150ms与100次采样/最大延迟。旧25次样本使用`statistics.quantiles`默认exclusive插值，一次209ms延迟尖峰会算出156ms，虽其余24次约25–33ms；不能用提高门槛或反复重跑代替修正统计口径。初次CI失败及后续结果见本轮交付。

- checkpoint里的待执行工具也是已计费响应，恢复时必须在执行pending前重查累计预算，不能只在新模型调用前检查；预算耗尽后的纯文字完成与待执行工具要区分。
- 调用部门归属应在usage reservation冻结，不是settle时查当前User。0029只从既有账本补已知历史，旧open预留的未知部门保留null。
- 五条是pending queue容量，不是累计确认插话上限；按checkpoint确认ID和顺序展示全部已注入消息，不能截断前五条或披露未确认领取。
- `/files?ids=`是精确版本查找，不能复用浏览列表的is_head过滤；常规列表仍只显示head。版本切换还要取消旧的before分页，并将API完整路径预览清单作为权威快照，避免旧分支残留。
- 轮询用完成后再排下一轮、取消及请求超时；旧扫描结果仅更新仍pending的文件，不覆盖当前收藏/位置。SSE的HTTP200空EOF也计入连续失败预算；等待结束移除abort listener。
- 有界搜索的空页带cursor表示尚未完成；UI须保留继续入口。浏览器测试等明确的查询结果，不能用未限定的option locator匹配尚未退场的最近列表。审查/验证见[本轮报告](docs/reviews/2026-10-25-single-node-review.md)。

## 2026-10-24 真实模型早标题验收

- 旧实现标题10秒包含主额度gate；真实提供商曾正文成功但标题超时永久为空。Round2已将10秒改为gate通过后的模型预算，gate仍由run截止/取消约束；catalog显式声明quiet关闭thinking，timeout/error/empty采用规范化prompt兜底。同请求复测成功不能抹去首轮失败，先测gate/TTFT/reasoning/content。见[发布记录](docs/progress/updates/2026-10-24-release-ui-features.md)。
- 人工持有五服务stack的临时启动器应保持父进程直到清理，不能假设启动器退出后子进程仍存活；复用隔离Portal目录须先核对源码并检查生产build，目录存在不代表 `.next` build可用。当前会话详情集合字段为 `turns`，不是 `runs`；聊天布局没有 `main` 标签。临时探针错误与产品失败分开记录。

## 开始新任务

- 先读 `AGENTS.md`、[当前工作](docs/progress/current.md) 和本文，再检查真实 worktree、branch、HEAD 与 `git status --short --branch`；保留已有修改。目标/计划/验收入口见 [项目进度](docs/progress/README.md)，不要把下方历史分支记录当作当前状态。
- Python 设置、engine 和目录在 import 时初始化。测试入口必须先设置隔离环境，参见 `platform/tests/conftest.py`；不要使用开发者 `.env` 或真实数据库跑清库测试。
- `AILAB_TEST_DATABASE_URL` 会清空指定 PostgreSQL 的 public schema。只传一次性数据库。
- 本轮完整审查记录：`docs/reviews/2026-10-23-full-review.md`。

## 2026-10-23 Runtime / OCR 最终集成陷阱

- 交付幂等命中也可能清理重试staging对象，路径校验必须在查旧结果/任何delete之前；不能只在新产物分支校验。
- 上游tool call id只在单次响应内有意义；跨响应复用会把后续exec/提问/交付错误去重。持久身份由平台Run+模型步数+本步ordinal生成，模型协议仍保留原id。
- 暂停释放并发槽，因此回答与resume必须和新Run创建共享用户锁、重查额度/容量与会话存活。等待问题必须阻止同会话新一轮；删除会话/项目也要取消waiting_user/interrupted。pause即结束attempt，不等传输ACK。
- 中断也是Portal的静止状态，不能用turn-meta出现作为“续跑完成”的E2E证据。等明确回复/真实终态；暂停SSE关闭后回答要按已显示seq重连，否则重复delta。
- 图片不能每步把base64复制进4MiB checkpoint；只保存授权文件引用，恢复时重新授权读归一化图。token预算按vision开销，离线模型也要兼容multipart文本回退。
- checkpoint step天然增加，不能据此断言工作区落后；在文件工具开始前持久dirty，快照成功后清除。GC用终态事务冻结expires_at及范围索引；SQLite join全历史checkpoint再排序会漏掉性能目标。
- 最终统一基线、双库/浏览器/真实Docker与性能证据见 `docs/reviews/2026-10-23-t07-runtime.md`。六个工具边界用BaseException模拟硬中断，不把它写成六次真实SIGKILL；真实SIGKILL提交后恢复和三槽SIGTERM另有测试。

## 2026-10-23 持久任务集成与中断接手

- 新文件处理分支（如OCR）不能只写裸 `OutboxMessage(topic,payload)`；T09生产者须用 `stage_job` 和 `JobEnvelope`，同步扩展kind、API claim/业务完成判断、Worker分派与ACK。只mock处理函数的测试会漏掉信封/认领/重投断层，必须覆盖生产者到消费者及重复投递。
- 合并文件里没有冲突标记不代表Git冲突已经解除：同时核对MERGE_HEAD、index unmerged与未暂存修改；验收前保留前任尚未完成的适配。
- T3运行因429失败后，CLI宿主进程仍可能存在。以activeRunId、终态和worktree提交/改动核对实际进度，不能把进程存在写成“子任务仍在跑”，也不能把子任务局部绿当作集成验收。

## 2026-10-23 全量审查踩坑与约束

- 预签名 PUT 在完成上传后仍能重用。完成必须先冻结独立对象，再哈希/扫描；不能把已签名的原始 key 作为最终可信对象。
- 普通上传、预签名 complete、沙箱产物都计入资料库配额。检查和写入需在同一个 `user_critical_section` 中；幂等重试先查已存在记录，再检查额度。锁内刷新此前加载的 ORM 用户/文件。
- `users.settings` 是整份 JSON 写入。开关更新必须串行化，不能让多个请求用同一个旧副本各写一次。
- Agent 的 token 上限必须在每个响应记账之后、执行工具之前检查，也涵盖最后回答、收尾回答和恢复执行已有用量。不能只在工具循环底部检查。
- SQLite 读回 datetime 会丢 tzinfo。对外时间统一从 UTCModel schema 输出明确 UTC，否则亚洲时区会误显示 8 小时前。
- Docker `.Id` 是配置 digest；`ctr images ls` 展示 manifest digest。镜像导入后用 CRI inspecti 的配置 ID 比较。`kubectl apply` 失败必须立即中止。
- Kubernetes readiness 必须探测 readyz，消费者退出或依赖故障返回 503。Broker provider 健康检查每次只执行一次；清单探针超时需覆盖外部调用。
- 浏览器上传请求进行中也要禁止发送；扫描轮询的旧响应不能覆盖/复活已删除附件。对话详情请求需防乱序，流需在新轮替换和卸载时取消。
- S3 Body 和浏览器 ReadableStream reader 都要在 finally 中释放；204 不应解析 JSON。

## 验证

Python 3.12，按 constraints 安装 `.[dev,postgres,e2e]`；Portal 使用 `npm ci`。Linux CI 运行完整测试和 GNU 沙箱命令。

```bash
cd platform
.venv/bin/ruff check .
.venv/bin/ruff format --check .
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/15 .venv/bin/pytest tests -ra --cov=ailab_platform --cov-report=term-missing
# 一次性 PostgreSQL 16 数据库，端口仅举例；这两个库都会被测试改写
AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/14 \
AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_review \
AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15432/ailab_migrations .venv/bin/pytest tests -ra
AILAB_E2E=1 AILAB_TEST_REDIS_URL=redis://127.0.0.1:16379/13 .venv/bin/pytest e2e -ra
cd portal
npm test
npm run build
```

仓库根目录另跑 `shellcheck -S warning deploy.sh tests/*.sh platform/scripts/*.sh`、`bash -n deploy.sh`，platform 中跑 `bash scripts/lock_deps.sh --check`。

macOS 的 mock 沙箱使用宿主 shell，需 GNU coreutils/findutils（stat -c、find -printf、touch -d）；运行时在 PATH 前加入相应 `libexec/gnubin`。预览失败用例刻意排除 LibreOffice，也必须保留选中的 GNU find。Prometheus 默认 process 指标依赖 Linux /proc，不能在 macOS 上强行断言；Linux 测试仍断言该指标。

浏览器 E2E 按仓库既有 Playwright 流程运行。人工浏览器检查优先 T3 内置 preview。E2E 使用临时副本构建 Portal，并自动结束子服务；不要把它当开发服务器复用。

## 2026-10-23 项目与资料库后端

- `0008` 给 conversations/files 添加项目外键时沿用 `0004` 的 SQLite 直接 `ALTER TABLE ... ADD COLUMN ... REFERENCES`；不要 batch 重建被 runs/artifacts 引用的表。带现存引用数据和外键开启的升级、降级、再升级已加入迁移测试。
- 项目删除与上传、预签名、产物登记、移动使用同一个 `user_critical_section`。`transition_run(..., commit=False)` 连同事件参与外层事务，避免提前提交释放 PostgreSQL 用户锁；一般调用仍默认自行提交。发消息在锁内刷新 conversation，防止等待期间项目已被删除。
- 单文件、批量和项目删除统一走 `library.delete_file_rows`，提交后才清理主对象、derived_key、text_key；项目删除还要将软删对话的 project_id 清空并解除外部文件的 parent_id。
- ZIP 使用非 seekable 小缓冲和 ZIP_STORED，按存储块输出；下载前关闭 DB session。当前 Starlette 不会在客户端断开时主动关闭同步生成器，`_ArchiveResponse` 在 finally 中显式关闭，且屏蔽取消以完成存储流清理。ASGI 2.3/2.4 断开测试已验证修复前红、修复后绿。
- 临时 PostgreSQL 测试实例初始化需显式 `initdb -E UTF8`；`--no-locale` 单独使用可能创建 SQL_ASCII，psycopg 返回 bytes 导致 SQLAlchemy 报 `TypeError: cannot use a string pattern on a bytes-like object`。
- 本轮验证：GNU coreutils/findutils 在 PATH；SQLite 全套 346 passed / 8 PostgreSQL 专用项 skipped，覆盖率 82.93%；临时 PostgreSQL 16 两个独立测试库全套 354 passed / 0 skipped；Ruff check/format check 均通过。测试服务已关闭，没有 stash 或提交。

## 2026-10-23 项目与资料库前端

- 项目 = 对话 + 文件的容器。侧栏「最近」只列不属于项目的对话（`GET /v1/conversations` 不带 project_id 即只返回 project_id 为空的）；项目内对话在 `/p/<id>` 里。
- 「围绕文件对话」用 `/?files=a,b`（文件同属一个项目时为 `/p/<id>?files=`）；Chat 读取后立刻 `history.replaceState` 去掉参数，刷新不会重复附加。
- 全局 `button.danger` 有粉底：菜单/批量条里的 `.danger` 按钮必须显式 `background: none`，否则未悬停也是粉色。
- 批量接口和 ZIP 最多 200 个；「全选」在「加载更多」后可能超过，前端已拦截。
- UI 人工检查：T3 内置 preview 访问 `next dev` 会卡死主线程（127.0.0.1 下 dev 资源被 allowedDevOrigins 拦截，localhost 也会超时）。改用 `.venv` 里的 Playwright 无头 Chromium 对 `localhost` 截图；临时 API 用 `/tmp` 下独立 SQLite + `AILAB_ENV_FILE` 指向不存在的文件，不碰开发库。

## 2026-10-23 资料库缩略图与轮询（后端第二轮）

- 图片解码仅在 Worker：扫描通过后生成 `derived/{id}/thumb.webp`，文件 meta 写 `thumb_key`；WebP 480×480 内、quality 75/method 4、第一帧、EXIF 方向、保留透明度。输入封顶 20 MiB / 1600 万像素（bomb warning 也当失败），SVG/矢量、损坏、编码/存储失败都跳过缩略图，文件仍 READY。先缩小再 EXIF-transpose，避免复制完整解码帧；方形边界下结果等价。
- 沙箱产物登记仅按 MIME/扩展名判候选，不在 API 解析；入 `thumbnail_only: true` 作业。此分支只处理 READY 且缺 thumb_key 的记录，失败重试或耗尽只能丢缩略图任务，绝不能套普通 pending 文件的拒绝逻辑。产物幂等登记重试也可补入队，队列异常仅日志。
- 回填（platform 目录，使用同一数据库/存储/Redis 配置）：`AILAB_QUEUE_BACKEND=redis .venv/bin/python -m ailab_platform.worker.backfill_thumbnails --batch-size 200`。只读 DB，按 id keyset 分批入队；独立 memory 队列不能交付，CLI 明确拒绝。已完成跳过、重复消息安全；损坏/超限文件再次执行仍会重试。通用 MIME 且无图片扩展名的历史文件不自动识别，避免回填读取每份原文件。
- `FileOut.thumbnail_url` 为 `/v1/files/{id}/thumbnail` 或 null。缩略图仅 owned+READY 可取，否则 404；200 为 image/webp、private 一年 immutable、SHA256+格式版本 ETag，If-None-Match（含弱验证器/列表/*）匹配返回 304。先关 DB，再流存储，断开用共享 `_ClosingStreamingResponse` 释放流。删除统一清理 thumb_key。
- `GET /v1/files?ids=a&ids=b&limit=200`：最多 200 个 ids，未知/非本人静默忽略，与现有筛选取交集；默认 limit 仍是 50，顺序仍按创建/收藏时间。
- 0008 尚未发布，直接补收藏/图片部分索引、(user_id, project_id, created_at) 与存活项目对话计数部分索引。SQLite 的图片部分索引必须用固定 LIKE 字面量匹配，绑定 `LIKE ?` 不会命中；用户 q 仍是转义绑定参数。新增索引后必须 ANALYZE 再比较计划，否则 SQLite 的旧统计可能使 all/q 误选项目索引。完整双库 EXPLAIN 与合成负载边界见 `docs/reviews/2026-10-23-library-query-plans.md`；未加不能加速 `%q%` 的普通文件名索引。
- 验证：新增 25 项回归；SQLite 全套 371 passed / 8 PostgreSQL 专用项 skipped，覆盖率 83.03%；临时 PostgreSQL 16 两个独立测试库全套 379 passed / 0 skipped，迁移升级/降级/漂移通过；Ruff check/format、`bash scripts/lock_deps.sh --check` 通过。仍有既存 Starlette/httpx 弃用警告。未改 portal、未跑前端/E2E、未提交/stash/发布；临时 DB/Redis 服务已关闭。

## 2026-10-23 资料库第二轮（缩略图与性能）

- 侧栏是 `position: sticky`，会形成层叠上下文：在侧栏里打开的弹窗即使 `z-index` 很高也会被主区域盖住。Modal 和会话「⋯」菜单都必须 `createPortal` 到 `document.body`；会话菜单还要用 fixed 定位，否则会被侧栏滚动区裁掉。
- `lib/projects.ts` 是全页共享的项目列表缓存。读取方同时挂载时复用同一个请求；只有 `projectsChanged()` 才会在请求进行中标记过期并再拉一次。把这两者混为一谈会导致每页重复请求（生产构建实测过）。
- Portal 代理原先对所有响应强制 `no-store`，连 API 的 immutable 预览图都缓存不了。现在会透传 `cache-control`、`etag` 和 `if-none-match`；API 未声明时仍是 `no-store`。
- 缩略图像素上限分格式：JPEG 走 `draft()`，最低按 1/8 比例解码，上限 128 MP；PNG/GIF/WebP 等需要全尺寸解码，上限 16 MP（Worker pod 256 MiB）。只设一个 16 MP 上限会让 24–48 MP 的手机照片都没有缩略图。
- 前端不会为了填一个小图标去加载大图原文件：没有缩略图时只内联显示 ≤256 KB 的图片（例如 SVG），否则显示类型色块。只有打开大图查看时才加载原图。

## 2026-10-23 PR 合并检查

- fork PR 的 CI 可能显示 `action_required`，而 PR 检查列表为空、合并状态为 `UNSTABLE`；这表示工作流等待维护者批准，不代表测试已通过。协作者权限生效后，先批准已有 workflow run，再核对实际检查结果。
- #26 基于 #25。依次使用 merge commit 保留提交祖先关系，避免 squash/rebase 后后续 PR 重复展示或冲突；每次合并前重新核对 head SHA、base 和检查状态。
- GitHub App 连接器与本机 `gh` 使用不同权限。连接器合并返回 `Resource not accessible by integration` 时，先确认本机账号仍有 WRITE 权限，再通过 `gh pr merge --match-head-commit <已验证 SHA>` 合并；不要将连接器的权限错误误判为协作者权限未生效。
- 资料库改版后要同步 E2E：上传 input 是隐藏的，以可见「上传」按钮确认登录；文件行是 `.lib-row`，可用后才出现带文件名的下载链接；文件/会话删除经「更多操作」菜单；个人技能显示在资料元数据链接中。扫描 mock 必须匹配带 ids 的批量 GET 并返回数组，继续断言上传/扫描期间不能发送、移除附件后旧响应不能复活附件。
- #26 原 CI 的 6 项 E2E 失败来自上述旧定位器/旧 mock。更新后本地生产构建 + 真实服务/独立 Redis 完整 E2E：34 passed；Ruff check/format、diff check 通过。未改产品代码。
- 技能开关是乐观更新，Composer 默认按钮也先于技能 GET 完成显示。E2E 不能用开关变色或 Excel 按钮出现判断保存/加载已完成：关闭和恢复都要等待 PUT 成功，并等待被禁用的 PPT 按钮实际从 DOM 消失。临时延迟 Chat 技能响应 750ms，旧断言稳定失败（1 failed），修复后同样延迟下完整 E2E 34 passed；不要用固定 sleep 或重跑 CI 掩盖这类竞态。

## 2026-10-23 W1 ops：存储、部署、备份与生命周期

- 分支 `arch/w1-ops` 从 `6666573` 建立；不推送。`Storage` Protocol 在 `ports/storage.py`，原 `storage` 导入仍可用；`put_stream(..., max_bytes=...)` 原子限额，`open_stream` 必须用 with（异常/提前结束也关闭），`list_prefix` 返回 `ObjectPage(items,cursor)`，最大 1000。仅遍历一页不会遍历全桶；workspace GC 已改分页。Local copy 先 pin 文件描述符，避免 signed key 被替换后读取不同 inode。S3 spool 使用磁盘并关 multipart 线程；尚未在真实 S3 验收。
- Redis `XAUTOCLAIM` 必须续用返回游标：每次从 0 开始会被前面的活跃 pending 永久挡住；`timeout=0` 不可传 Redis `BLOCK 0`（无限阻塞）。内存队列也序列化 payload，避免生产者/消费者突变影响重投。
- 迁移 `0010_lifecycle.py`（合并后编号）新增 `lifecycle_deletions` 与 GC 索引，并去掉 model_usage → runs 的外键；会话 purge 清理 RunEvent/Artifact/Run，保留账本（见下文“合并 ledger 与 lifecycle”）。
- GC 按用户锁检查 presign，至少等签名 900 秒加宽限；完成后的独立 obj_* 冻结对象不作为原始 staging 删除。DB 删除与对象删除意图同事务提交，存储失败重试；会话和事件分批，prefix 物理删除有总预算。固定指标 kind，不放用户/run/file。配置默认：batch100、间隔3600s、presign/orphan86400s、events/conversation30d、trash30d（只预留），workspace 仍3d。详见 backup-restore runbook。
- 备份 SQLite 用 online backup；PG 导出 snapshot 给 pg_dump 与引用清单共用；对象 JSONL 按块复制/哈希，不硬链接，脱敏 models.toml 的字面凭据含注释也必须清掉。`backup.json` 只在全部 verify 后写入。恢复拒绝非空目录；PG 先恢复 SQL，运维显式导入新空库。跨 DB/S3 不能原子快照，确定性备份窗口要停写/GC；RPO/RTO 必须实际数据演练，不能沿用目标架构数字。
- 部署改为 `platform/deploy/base` + `overlays/dev|lite`；lite 命令仍 `sudo ./deploy.sh`，`--dry-run` 无需集群/不会部署。API NodePort 改由 public nginx 网关 allow-list `/v1/`；内部服务 ClusterIP，Broker DNS 独立端点。base NetworkPolicy 用 k3s 自带控制器；lite Broker hostNetwork 不受可移植 Pod policy 保证，8081/8090 需宿主防火墙。dev PG 独立；lite 保留 SQLite 数据兼容，独立升级 DB 要迁移。ClamAV slot 在 base/scanner，C 加资源与 Worker-only policy。PVC lite40Gi/dev20Gi，旧 local-path PVC 不能假定可扩容；容量设置须与实际盘/PVC一起核验。
- 仍是整份内存的路径：Broker 注入运行附件与资料库渲染输入（经 HTTP 取整份 bytes，上限为上传上限 50 MiB，Broker Pod 384 MiB）、交付 100 MiB、workspace 60 MiB。Worker 已改为流式下载到临时文件再交给受限子进程。整机 RSS 未在目标机器上测；Local 原子写崩溃留下的临时文件、冻结后 DB 失败且删除也失败的主对象，仍需后续通用孤儿扫描收敛。
- 本机没有 Docker daemon，MinIO 官方 darwin-arm64 下载返回410；S3 真实契约跳过，CI 独立 MinIO service-container job 已配置但未远端执行。kubectl 三个 overlay 渲染通过，`apply --dry-run=client --validate=false` 仍需 API discovery，报 localhost:8080 connection refused；无真实集群部署/NetworkPolicy/防火墙验收。
- 最终验证：纯 SQLite/Local/memory 全套 **401 passed / 37 skipped**；临时 PG16(15450)+Redis(16450) 全套 **429 passed / 9 skipped**（8 S3 + 1 memory 不适用 Redis 游标）；契约分项 Local8、memory2、Redis3、SQLite DB3、PG DB3、GC双库各7+容量1、备份5（SQLite恢复/损坏/引用/脱敏+PG真实导入）。Portal4测试与生产构建、Ruff check/format、ShellCheck、bash -n、依赖锁一致性通过；无 UI 改动未跑浏览器E2E。Local50/100MiB探针 Python峰值148697B、RSS增278528/688128B、0.15/0.38s；非目标小机/整机验收。临时服务均需在交付前停止。

## 2026-10-23 调用级账本与硬用户配额

- `0009`：先 reserve、再调用、最后 settle；`call_id` 是幂等单位，真实重跑必须新 ID。旧 blind usage POST 返回 410，API/Agent 与服务鉴权 allowlist 要一起切换。新内部路由为 `POST /internal/usage/reserve`、`POST /internal/usage/settle`、`GET /internal/runs/{id}/usage`；完整 Portal/服务契约见 `docs/architecture/usage-ledger.md`。
- 用户锁内只做检查与数据库提交；日/月用已结算整数 milli + 全部 open 预留。过期不等于释放，reaper 每轮最多 200 条转 unreconciled；晚到 settle 原行修正，终态 Run 仍能补账。取消只有确认未发出模型请求才零费用释放，已发出的中断计 estimated/settled；408/5xx 不能当作确定未消费。
- SQLite 迁移绑定 Decimal 必须给 `sa.column` 声明 Numeric，否则报不支持 decimal.Decimal。迁移按 500 条 keyset 冻结旧缓存折扣与金额，legacy 不随配置重算。SQLite 无时区、PostgreSQL 会返回服务器时区；幂等响应时间都要显式转 UTC。
- `Run.result.usage_totals` 是账本聚合快照。状态提交与旧工作区清理写整份 result 时也必须持同一用户锁、刷新并合并，否则晚到结算总数会丢；已补红绿回归。用量页面必须同一 SQL snapshot 读取 settled + reserved，分开 SELECT 会在结算切换期间双计；CLI 对账总数同样用单条投影/聚合查询。
- 运行预算仍为原始 input + output 的软上限，reasoning 已包含在 output；Agent 从账本恢复总数，记账后、工具前拦截下一步，已完成最终回答跨预算仍成功。计量请求有 call/run 头和 max_tokens；模糊失败不得在同一个预留下偷偷再发一次推理。模型专用 tokenizer、真实上游 max_tokens 强制执行未验收。
- 成本：每模型调用 2 次事务、4 个业务写（比旧路径多 3 写/1 commit），不新增依赖/后台服务。300 调用本机测量，SQLite/PostgreSQL 预留锁 p95 1.10/1.87 ms，结算锁 p95 1.23/2.26 ms；200 条过期回收 247/377 ms。8,000 行双库 EXPLAIN 检查窗口、open、过期和对账索引；按用户周期 SUM 仍随历史行数增长。
- 最终验证：Ruff check/format 通过；SQLite + Redis 全套 405 passed / 11 PostgreSQL 项 skipped；PostgreSQL 16 + Redis 全套 416 passed / 0 skipped；Portal 4 tests + production build、Chromium E2E 34 项通过。临时服务只用 16410/15410，结束后关闭；未改 .env/开发数据库，未推送或创建 PR。

## 2026-10-23 合并 ledger 与 lifecycle

- 账本不引用 runs：`model_usage.run_id`、`usage_reservations.run_id` 是普通字符串，不是外键（0012 删除旧 FK）。软删对话 30 天后清除 runs/events/artifacts，但它们的用量仍计入额度窗口、管理报表与对账；不要再在 purge 里删 `ModelUsage`。SQLite 上 FK 无名，迁移用 batch 的 `naming_convention` 命名后再删；model_usage 无表引用，batch 重建安全。
- 迁移链最终见“合并 C / G / I 的集成点”。

## 2026-10-23 T03/T05 严格配置与服务身份

- `AILAB_PROFILE` 优先；旧 env：test→test，dev/smoke/k3s→dev，其余→secure。k3s 清单保留显式 dev 兼容，不能据此宣称已安全上线。secure 同一启动门禁检查 mock、stub、默认秘密、共享令牌、身份、注册和外部模型；ClamAV 仅定义 scanner_backend，实现在 C 分支。
- 不给被调方分发调用方原始令牌：每进程只挂 `platform-{service}-identity`，共同的 `AILAB_SERVICE_CREDENTIALS` 仅含 sha256 verifier。Secret 脚本重跑保留令牌；读取身份失败不能当作不存在后重建。旧共享令牌仅 dev/test 警告兼容，真实 E2E 已禁用它。模型 Secret 仅 API/Agent，OSB key 仅 Broker。
- Uvicorn 默认会先解释 XFF 并改写 peer；只改登录 handler 不够。所有仓库启动入口 `--no-proxy-headers`，应用按 `trusted_proxies` 从右向左验证。默认不信代理；部署应填实际 Portal/反代 CIDR。
- 内部 httpx 必须 `trust_env=False`：本机环境代理曾截走内部请求，导致 E2E 个人技能 Run `httpx.ReadError`。关闭后完整 E2E 通过；模型等外部请求不在此修改范围。
- FastAPI 0.156 的 `include_router` 是延迟挂载，顶层 app.routes 只遍历 APIRoute 会漏掉全部业务路由。scope 巡检须读 effective_route_contexts；新增/撤掉 scope 的测试覆盖 included prefix。
- Agent/Broker 文件请求必带 run_id；仅同用户、活跃 Run、附件/同会话材料/产物，个人技能为 API 明确列出的系统附件。Broker service.py 与 Agent loop.py 只改凭据和上下文传递。Worker 的 READY 派生任务带 job=thumbnail/preview，不能写 scan/status；已处理/删除文件的普通重投须 ACK，认证失败须重试，包括 poison 分支。
- 并行合并：A 的 reserve 用 `Depends(require_scope("usage:reserve"))`，settle 用 usage:write；C 的 READY 派生回写用 files:preview_write 并保留 job/状态/字段限制，Worker→Broker render 仅 sandbox:render，不能开放 sandbox:use；通用调用可用 `PlatformClient(service="worker")` / `AsyncBrokerClient(service="worker")`，不要加载其他服务令牌。scope 表和完整设置见 ADR 0002。
- 无迁移。超出文件 ownership 的最小改动：api/main.py（启动门禁身份、日志脱敏）、agent/loop.py、broker/service.py、worker/pipeline.py（调用凭据/上下文）、queue.py（拒绝未知工厂值）、dev_up.sh（禁止 Uvicorn 预处理 XFF）、tests/E2E（新契约）及本文/ADR/status-matrix。
- 最终验证（GNU PATH；仅 Redis 16420、PG16 15420 的一次性库）：SQLite+Redis 424 passed / 8 PG 项 skipped（49.43s）；PG+Redis 432 passed（55.05s）；四服务独立身份、共享令牌禁用的 E2E 34 passed（81.37s，含 Portal tests/build）。Ruff check/format、ShellCheck warning、bash syntax、lock_deps、diff check 通过。基线配置替换回归 18 failed，移除 XFF/Run 文件绑定 2 failed，恢复后绿；路由无 scope 的 direct/included 测试均被 guard 拒绝。固定 5 verifier 的认证微基准 100,000 次约 3.04 µs/次；双库新文件绑定查询 EXPLAIN 使用既有 Run 索引，无新增迁移/后台任务。临时服务验收后关闭；未推送。真实 k3s、TokenReview/TLS、ClamAV、Docker/OpenSandbox、S3 不在本分支验收范围。

## 2026-10-23 合并 service-auth 与 deploy 重构

- B 修改的 `platform/deploy/k8s/platform.yaml` 已被 E 拆成 `deploy/base` + overlays；每个服务只挂自己的 `platform-<svc>-identity`、共享 `platform-service-verifiers`，api/agent 另挂 `platform-model-secrets`，原 `platform-secrets` 只按 key 单取 invite/dev token/admin 密码/OSB key。所有 uvicorn 加 `--no-proxy-headers`（XFF 只信 `trusted_proxies`）。
- 渲染 overlays 需要 kustomize ≥ 5.7（k3s 1.34 自带 kubectl 内嵌版本即可）。kustomize 5.4.3 处理 lite overlay 中顶层 `$patch: delete` 会 nil pointer panic，不是清单错误。
- 新增内部端点必须 `dependencies=[Depends(require_scope(...))]`，路由巡检测试会拦截遗漏；ledger 的 reserve 用 `usage:reserve`、settle/旧 usage 用 `usage:write`、GET run usage 用 `runs:read`。

## 2026-10-23 Broker 租约恢复与准入（W1）

- 启动不再删光租约。Provider 创建即写 owner/lease/run/user/created_at，Redis CAS 保存期望状态；有效资源重启接管，Redis 清空从资源元数据重建。`releasing` 删除失败保留记录和容量，2→60 秒退避；不能把连接失败当作资源不存在。
- Docker label 不可变：续租/状态放在容器 root-only `/run/ailab-lease.json`，不能放进用户 workspace/快照；OSB 用 lifecycle metadata。Docker 无原生 TTL，Broker 宕机期间不会自动回收；disk 规格仍未落实文件系统配额。
- `acquire(SandboxSpec, LeasePrincipal, *, wait_seconds=None, on_queued=None, prepare=None) -> LeaseHandle`，`release(handle)`；C 的 render 必须 `system=True/profile=render`，ttl/max_lifetime 都≤120 秒，memory≤保留内存。文件/exec 传 `handle.lease_id`。完整接口、错误、设置见 ADR 0003。
- 普通/短 render 分保留槽与内存，符合条件者 FIFO，单用户超限不堵其他用户；队列上限32、截止300秒，释放通知唤醒，位置通过事件/`GET /internal/admission/{run_id}`暴露。队列重启后需调用方重试；不重放工具、不做 idle reuse。
- Agent 对 `POST /internal/leases/{id}/renew` 每60秒心跳；最长4小时，默认TTL20分钟。`lease_lost/lease_expired` 为410可恢复错误，runtime 后续应终止当前轮并按安全checkpoint恢复。现有 runtime 未在本分支接入心跳。
- 同owner仅一个Broker/一个uvicorn worker，Redis TTL30秒锁与独立心跳；第二实例启动失败，失锁停止服务。Redis flush 恢复会先对账再准入；这不是分布式选主/HA。对账列表是快照，新创建资源必须再inspect，不能误判丢失。渲染更新缓存必须合并最新CAS版本，不能覆盖同时心跳。
- 配置默认总槽位改2（含一个render保留槽），总内存3GiB，render768MiB；仅改 `.env.example` 与部署清单的相应总数，合并时与B/E的profile/部署配置对齐。自定义总槽1需显式关闭reserve或增大总数。无业务DB迁移。
- 故障测试仅允许flush本轮临时Redis **16440**；真实Provider通过 `AILAB_TEST_DOCKER=1` / `AILAB_TEST_OSB_URL`选跑。性能诊断：2个mock资源、200次对账，真实Redis每次2.476ms wall/2.038ms CPU、峰值Python临时分配226.3KiB、进程RSS49.2MiB；不代表Docker/OSB或生产2vCPU实测。本轮最终验收：SQLite+Redis 449 passed/10 skipped，PostgreSQL16+Redis 457 passed/2 skipped（仅Docker/OSB真实契约未启用）；含48项内存/Redis故障测试，Portal 4项与生产build、Ruff check/format、依赖锁一致性均通过。临时Redis16440、PG15440验证后关闭。

## 2026-10-23 W1 扫描隔离与资料库预览

- 分支 arch/w1-worker-preview，基于 6666573。协议与合并清单：docs/reviews/2026-10-23-worker-preview.md；状态表已补建。没有改 Portal UI、LeaseManager 内部，也没有推送。
- scan_unavailable 绝不能当 clean：文件保持 pending，默认 3 次、5/10 秒退避，用尽写 scan_exhausted；清空标志并重新入队才恢复人工重试。thumbnail_only/preview_only 分支均不能回退 READY。
- 不可信文档/图片/ZIP/skill 检查进受限子进程；Linux AS 192 MiB、CPU 45 秒、墙钟60秒、输出16MiB，macOS AS 不可靠，改 RSS watchdog。Worker Pod 改384MiB，避免只预算子进程而漏算父进程。进程不继承服务密钥环境；ZIP先查2048项/128MiB/200倍限制。
- read_only Excel 可能没有 max_row/max_column（write_only 生成的文件就是例子），必须 calculate_dimension；逐格 ws.cell 会反复扫描 XML，改成两个 iter_rows 流并行读。复用 renderer.sheets(recalculate=False)，Worker 不能因为宿主有 soffice 就调用它。流式模式的列宽/合并/图表元数据暂为空，详见审查记录。
- 短 Unix socket 路径用 /tmp；pytest 的 macOS tmp_path 可能超过 AF_UNIX 的路径长度。OOM 用大量小 XML 节点触发分配；单个巨型文本节点会先撞 libxml 的文本限制，不能用该 corrupt 冒充内存验收。
- Office/PDF 按需沙箱渲染（合并后改为 `acquire` render 保留槽 + 预览任务绑定，RenderContexts 已删除）。57页 PDF 验证只产40页并返回总57页。SVG original 强制 CSP sandbox，文本固定 text/plain。
- 迁移 0011_preview 建 outbox，manifest 仍在 files.meta。预览请求和副作用同事务，派发失败留记录。API内部文件meta回写补用户锁，避免缩略图回写覆盖同时请求的preview。
- 本机测量（合成样本，非生产承诺）：20MiB PPTX 子进程峰值93.9MiB、父进程峰值80.3MiB（峰值和174.3MiB保守预算），10MiB XLSX 子34.6MiB、父79.5MiB（和114.2MiB）；含 Worker import 的耗时1.122/4.096秒。单页 DOCX mock+真实Office工具2.625秒、输出23,437字节、租约全释放；原始 JSON/复现脚本已入库。
- 最终验证：SQLite + Redis 435 passed / 10 skipped（9项PG专用、1项真实clamd）；PG16 + Redis 444 passed / 1 skipped（clamd）。Ruff check/format、依赖锁一致性、diff check通过；Portal 4测试与生产build通过。未启动真实clamd/Docker/OpenSandbox/k3s，未验证Linux AS与生产内存。测试仅使用本任务Redis16430、PG15430一次性库。

## 2026-10-23 身份 / 管理 API / OIDC（T13 / T17）

- 迁移 0012_identity；SQLite users 只 ADD/DROP COLUMN，不 batch 重建。带现有 Run/Token、部门父子和审计，开启外键的升/降/再升及双库 schema drift 通过。
- Principal 附在 request.state，current_user 仍返回 User；认证角色 joinedload 保持原会话列表 ≤3 次 SQL，不能用额外 selectin 查询放宽旧性能门槛。
- Next 的 req.url 可能把 127.0.0.1/LAN 主机归一成 localhost；OIDC 回调必须返回相对 Location，绝对本地重定向会让 Cookie 跨主机失效。跨站 IdP 回跳已用生产 Portal + Playwright 红/绿验证；流程 Cookie 用 Lax，平台 Cookie 保持 Strict/httpOnly。
- 部门/策略保持数组响应，keyset 下一页在 X-Next-Cursor；Portal 通用代理必须透传。dept_admin 范围进入 SQL，不能取全量后过滤；无任何管理员内容读取特权。
- 本基线 ModelUsage 无 dept_id。合并 A 后补 usage 写入快照、冻结 weighted_amount、全局报表索引，并把 limits_for 接入 storage/concurrency/summary；本分支仅改 token 准入一个点。详见 `docs/reviews/2026-10-23-identity-implementation.md`。
- OIDC 上游令牌不出 API；只按 iss/sub 建联，不按 email 自动合并。真实 Keycloak 测试由 AILAB_TEST_OIDC_ISSUER gate，未配置即跳过，不把 fake issuer 验收称作企业 SSO 验收。
- 本轮最终验证：SQLite+Redis 463 passed / 10 skipped；PG16+Redis 472 passed / 1 skipped；浏览器 E2E 34 passed；Portal Node 9 passed、build、Ruff、依赖锁一致性通过。真实 Keycloak gate 未配置。临时服务仅用 16470/15470，结束后关闭；无开发库操作。

## 2026-10-23 T18 文件版本与回收站（arch/w2-file-versions）

- 基线 `609c1d3` 实际未对同名产物设置 parent/version；本轮在 `user_critical_section` 内按同会话同名产物找链、按整链 max(version)+1 分配。上传从旧版分支也必须取整链最大号，不能 parent.version+1；unique(lineage_id,version) 与单 head 部分唯一索引兜底。
- 迁移 0013_file_versions。SQLite 只 ADD/DROP COLUMN，不重建 files；旧 parent 链回填根 id，保留全部 file id/FK。历史重复版本号按 (version,created_at,id) 稳定重编号；断链/跨用户/环拒绝迁移。迁移仅改元数据，不连接对象存储；既有超限链在下次写版本时收敛。降级不恢复旧重复编号。
- 默认 `max_file_versions=10`、`trash_days=30`（配置范围校验）。超限只 tombstone 旧行、提交后清对象，旧 Run/Artifact 引用仍在、content=410。恢复旧版独立 copy 原对象及 derived 前缀，递归改写派生 key；保留扫描状态，不能把 pending/rejected 复制成 READY。回收站里的版本回退保留回收站状态，显式 `/restore` 再解除。
- 普通/批量 delete（兼容 alias）/项目删除均整链入站；trash 继续计配额，连回收站的 rejected 文件也计；purge 才释放。移动/收藏作用整链，项目计数只算 live head。项目删除分批 200 个 lineage，清 FK 并记 trashed_from_project_id；已经入站的链也要解除 FK，不能让首批更新误清其他批次的 project_id。
- `DELETE /v1/trash` 按 200 批次清理，请求开始时间作截止，避免追逐不断新增的删除；count 是 lineage 数。batch count 保持输入去重后 id 数。cursor 是上一页末项 file id；保留原 offset。`purge_expired_trash` 每轮最多 200 head，锁内再次确认过期，集成到既有 reaper；函数独立，方便与 E/A 合并。
- 物理清理唯一入口仍是 `delete_file_rows`，包含登记的 key 和 `derived/{id}/` 全前缀。保留原提交后 best-effort 删除；对象存储故障/提交后 kill 的孤儿收敛待 E/T09 GC/outbox 集成，未声称生产级故障验收。真实 S3 拷贝的 RSS/磁盘/时延未测。
- SQLite 要在 head 部分索引键里覆盖 is_head/deleted_at/purged_at（图片还含 content_type）才显示 COVERING INDEX；PG 为 Index Only Scan/Heap Fetches=0。列表先索引取 id，再批量取详情和版本数，禁止 N+1；substring q 仍需读候选行。10 万行原始计划和成本追加在 library-query-plans 文档；新索引约 12 MiB（PG），无新依赖。
- 保留 files_router 的 `/v1/files` 前缀，另注册 trash_router；不要改前缀让 C 后续新增预览装饰器误挂到 `/v1/{id}/preview`。本轮只在已有 thumbnail 路由补 purged guard，未实现或覆盖 C 的新 preview 路由。`routes_runs` 附件与 `routes_skills` 个人技能需同步过滤 trashed/purged；否则删除文件还能被新消息/技能引用。
- 分页取 id 与详情是两个查询，期间可能移入站、换 head 或 purge；第二次读取必须重新过滤可见性并忽略已消失 id，否则会 KeyError/500。用 SQLAlchemy after_cursor_execute 注入真实的交错删除回归覆盖。
- 并发回归不要在每个线程启动 `with TestClient(app)` lifespan：Alembic 全局代理会互相覆盖而报 KeyError/config；共享测试外层 lifespan，线程只创建无 context 的客户端发请求。reaper 测试必须只让自己的两份文件到期，避免误算同进程其他用例留下的用户回收站。
- 行为红绿：隔离的 `609c1d3` 副本 3 项（删除留对象、只列 head、恢复旧版）均红，当前实现同样 3 项均绿。完整验证与最终计数见下方本轮验收记录。未 push；临时 PostgreSQL 15490 / Redis 16490 仅本轮使用，结束后关闭。

### 本轮验收记录

- Python 3.12 / GNU coreutils+findutils：SQLite + Redis `414 passed, 11 skipped`（10 项 PG 迁移及 1 项 PG 并发条件跳过）；临时 PG16 + Redis `425 passed`，包含现存 parent 链/FK 的 upgrade → downgrade → upgrade、模型漂移、历史重复号及跨用户链检查。
- 浏览器 E2E `34 passed`；最后分页竞态修复另复验上传 → 回收站下载 → 恢复 → 最终 purge：`1 passed, 28 deselected`。E2E 临时副本 production build 通过；Portal Node tests `4 passed`。
- Ruff check / format check、依赖锁一致性、git diff --check 均通过。测试与计划只用独立临时目录、PG 15490 和 Redis 16490，未读取开发者 .env/var 数据。
- 临时 PostgreSQL/Redis 已关闭，15490/16490 已确认无监听；无 push。

## 2026-10-23 合并 C / G / I 的集成点

- 迁移链已整理为单调编号：0008 → 0009 ledger → 0010 lifecycle → 0011 preview outbox → 0012 identity → 0013 file versions。模型漂移检查必须在 head 上做（`run_migrations`），升到中间版本再比对全量 models 必然失败。
- 资料库渲染：Broker `LibraryRenderer` 用 `acquire(SandboxSpec(profile="render"), LeasePrincipal(system=True))` 走准入的 render 保留槽，不得再替换 `manager.platform` 或伪造 Run。Broker 读文件以 `job=preview&preview_job=<id>` 绑定到 API 记录的当前 pending 预览任务（scope `files:read_for_preview`）；Worker 的缩略图/预览任务同样要带 `job`，否则 B 的绑定会 403。
- 额度：统一走 `quota.user_limits`（用户策略 → 部门策略 → 默认策略 → settings），覆盖 token 预留、资料库空间、并发 Run 和用量摘要；不要再直接读 `settings.*_limit`。
- 用量报表只读账本：`weighted_amount` 是 milli 单位（÷1000 才是加权 Token），部门取调用时快照 `model_usage.dept_id`，不 join runs（对话被清除后用量仍在）。
- 删除：`DELETE /v1/files/{id}` 现在进回收站，派生对象保留到 purge。`library.delete_file_rows` 在同一事务写 `lifecycle_deletions` 持久意图，提交后仍立即尝试删除；失败由 lifecycle 的 `drain_object_deletions` 重试。回收站过期清理在 `lifecycle_once` 中（每小时），不在每分钟的 reaper 里。
- 内部读文件统一经 `_bound_file`：回收站中的文件 404，已清理版本 410。
- 资料库界面 E2E 在 `platform/e2e/test_library_ui.py`；设 `AILAB_E2E_SHOTS=/tmp/x` 会保存各状态截图供人工检查。macOS 上 mock 沙箱用宿主 LibreOffice 渲染，宿主未配置 CJK 回退字体时中文字形会被丢掉（只剩英文与线条），这不是渲染器问题；生产沙箱镜像装有 Noto CJK 与微软雅黑/宋体别名。
- 全局 `button` 样式带 `align-items/justify-content: center`。把整张卡片或整行做成 button 时要显式覆盖（`align-items: stretch; justify-content: flex-start`），否则内容居中。

## 2026-10-23 真实组件与原生 ARM64 验收

- 工作树 `t3-4a3b78af`，分支 `verify/real-components`，基线 `feat/arch-hardening@9c0c808`；仅本地提交。完整命令、数据与未覆盖边界：`docs/reviews/2026-10-23-real-components.md` 和同目录 `real-components-20261023/`。
- Colima 只用专用 profile `ailab-verify`（2CPU/8GiB/60GiB/k3s），必须 `--activate=false`，Docker 显式 `--context colima-ailab-verify`，kubectl 通过该 VM 的 `sudo k3s kubectl`。宿主缺 kubectl 时 start 在建 VM 前失败；Colima 的 Mac `/tmp` 不共享，bind 会成为空目录，必须用共享用户目录。中断后 profile 曾停止，原采样未落盘，不能把不完整数字当结论；重启后所有 PID/映射和 OSB 进程要重新建立。
- `clamav/clamav:1.4` 缺 arm64，不能据此说 ClamAV 没有 ARM64。用户指出 `clamav/clamav-debian:latest`，实测 1.5.4 原生支持，最终清单固定 multiarch digest。冷启动先等签名与 PING；曾用的 amd64 模拟数字不用于最终性能验收。原生契约通过，pause→pending/scan_unavailable 30.027s，unpause→ready 36.858s；这是进程停滞恢复，未测重新下载/加载签名。
- MinIO Docker Hub 指定镜像不可拉，Quay 401、官方二进制410；本轮固定官方源码 tag 构建真实 MinIO。CI仍引用旧DockerHub tag，待维护镜像源，不把本地源码构建通过当CI通过。PostgreSQL16 restore 用PG16客户端；PG17 pg_dump 会写 transaction_timeout 导致向PG16导入失败。
- OSB metadata 被按Kubernetes label校验：时间戳、JSON、preview:job_id 都不能原样传。adapter逐字段base32分块/读回解码，owner普通值保留可筛选，PATCH缩短须忽略旧尾块。真实OSB/Docker生命周期、Broker重启/移除该owner Redis记录/外部删除资源均通过。
- Linux RLIMIT_AS下libxml C层分配失败是XMLSyntaxError/ERR_NO_MEMORY，不是Python MemoryError；按结构化错误码归为memory_limit。原真实OOM测试红→绿，不能把corrupt当作OOM验收。
- 当前Chromium的IdP302回调不经过Playwright route拦截，Keycloak gate需真实本地callback listener，不能打印授权码。Keycloak realm导入文件必须匹配 `<realm>-realm.json`。Portal实际登录、组→角色/部门、Strict/httpOnly、停用/注销均已验。
- Redis健康检测不能复用业务队列15s超时和自动重试：本轮真实暂停Redis超过10s探针预算，独立1s connect/read无重试pool修复；API按既定合同返回200/degraded，Agent/Worker返回503，恢复均200。每个RedisQueue最多多1连接，无新增依赖。
- 原生负载：50MiB上传3.600s；20MiB PPTX扫描+解析27.906s；DOCX上传到预览9.057s；Broker注入50MiB 3.570s/peak RSS265.4MiB，交付100MiB 3.020s/284.4MiB，limit384MiB。Worker peak173.2MiB、clamd容器1084.7MiB，无OOM但有CPU节流。Broker整文件缓冲仍是设计债务，单次通过不证明并发安全。采样0.1s、886样本、采样器CPU1.884s；健康探测修复后另采5s空闲RSS，未重复全部负载。
- k3s备份本轮57对象/252762640B，暂停四个写服务后create/verify/restore/verify共4.14s、RSS60.1MiB；空目录恢复API的100MiB下载哈希、JPEG预览、Run均通过。不是生产RPO/RTO或异机介质验收。
- 最终Linux PG16+Redis+全局S3+原生clamd全套753 passed/9 skipped/101.26s；真实provider/Keycloak gate另跑通过；Portal11测试与production build通过，Ruff通过。未验真实OSB Office+S3联合部署、完整mock故障矩阵的真实版本、并发/长期负载、hostNetwork公网防火墙、gVisor/付费模型。清理状态见验收证据cleanup.log；只停止不删除专用profile。
- 清理最终状态（15:26 +08）：专用platform namespace/PVC、临时容器/卷、自建镜像/悬空构建层均删除，OSB/Portal/API/转发均停止；Colima ailab-verify=Stopped，profile与上游基础镜像保留；私密token/kubeconfig副本已删除。本地cache保留有限数据备份与证据，没有任务服务运行。
- Redis 健康检查用独立短超时客户端，但不要给它的连接池设上限：redis-py 的普通连接池满了直接抛错（不排队），readiness、liveness、metrics 同时 ping 时会把健康的 Redis 判成宕机（`tests/contracts/test_queue_health.py` 有并发回归）。

## 2026-10-23 main 集成与跨 session 接续

- main 的 SearXNG 改动原在已删除的 `platform/deploy/k8s/platform.yaml`；解决 modify/delete 冲突不能只删旧文件。检索资源已迁入 `base/search`，共享 config 显式设 `AILAB_SEARXNG_URL=http://platform-searxng:8080`（默认是 localhost），default-deny 下另加仅 Agent 可访问的 ingress。回归见 `platform/tests/test_web_deployment.py`，缺失配置时红、集成后绿。应用代码升级不额外重启检索服务。
- 新 session 以 `docs/progress/current.md` 接续；历史验收留在 reviews/updates，计划在 roadmap/design，能力状态只在 status-matrix。用户已要求剩余功能先暂停，不能把设计文档里的阶段顺序当作开工授权。
- Broker 并发 OOM 是按单次峰值推算的风险，现有证据没有双请求并发 OOMKill 实测；报告应区分测量值与推断。Redis health pool 后续在 `826899c` 去掉了连接数上限，旧真实组件报告中的“最多1连接”只描述当时版本。
- 发布包含 workflow 的分支时，HTTPS OAuth token 可能缺 `workflow` scope；本轮已验证当前账号 SSH 能写仓库，用显式 `git push git@github.com:shijunan126-creator/ailab-lite.git HEAD:feat/arch-hardening`，并核对 GitHub ref SHA。不要通过删 CI 变更绕过权限；临时 `-c remote.origin.url=...` 可能叠加原 URL，导致先报 HTTPS 失败再 SSH 成功，不能仅凭整体退出码判断远端结果。

## 2026-10-23 CI 结构更新

- PR 范围必须用 base...head 全量 diff，不能只看最后一次 push：连续推送取消旧运行后，若仅看文档补丁就可能漏验前面的代码。删除/重命名用 no-renames 保留两个路径；未知路径全套，Skill Markdown 不属于文档跳过白名单。
- E2E fixture 已执行 Portal Node tests 与隔离 production build；不另起重复 build job。双库 matrix 不互相 fail-fast，S3 不依赖 Python 的成败；最终稳定 `CI` 明确检查选中项，不允许意外 skip 变绿。
- `test_parser_wall_timeout_kills_child` 原来对极小文件设 1ms 超时；Linux coverage 下父进程可能晚于 child 退出才被调度，测试不一定触发 watchdog。现在启动真实阻塞 child，验证超时和 SIGKILL/reap，不靠解析器速度。
- Actions 的 job 级 env 不允许 `runner.temp` context，改在 step 写 GITHUB_ENV；actionlint 会捕获此类表达式错误。CI MinIO 不再拉失效 tag，改官方固定源码+SHA256与二进制缓存；版本/校验和/冷缓存验收必须同步。

## 2026-10-23 GitHub 首页 README 优先级

- GitHub 首页优先读取 `.github/README.md`，然后才是根目录 `README.md`、`docs/README.md`。新增 CI 说明时误用 `.github/README.md`，导致原项目说明虽然没删却从首页被替换。CI 说明改为 `.github/CI.md`；今后不要把普通维护文档命名为 `.github/README.*`。可通过 GitHub `GET /repos/{owner}/{repo}/readme` 返回的 path 核对实际展示文件。


## 2026-10-23 F8 Broker 流式传输

- `PlatformClient.download_file` 复用 Worker 流式路径时必须增加 run_id / preview_job 绑定，不能为了流式化丢服务授权上下文。API 错误页也按小块读取，避免错误体完整缓冲。
- OpenSandbox 1.1.1 的公开 `files.write_file(path, binary IOBase)` 与 `read_bytes_stream` 已支持流式；前者按64KiB读，后者需要显式 close（超限也一样）。不要传 bytes 或调用 read_bytes。新上传先写同目录随机文件，成功后 mv；清理失败不能掩盖原传输错误。
- 临时文件放 data_dir 的磁盘卷，不能默认放 /tmp（可能是tmpfs）；按owner隔离，单Broker成功领取所有权后回收前任崩溃遗留。每次正常/异常/取消均关闭流并删除本地spool。`CancelledError` 是 BaseException，acquire 只捕获 Exception 会留下 creating 沙箱；已补红绿回归。
- 文件 stat 只是预检：交付过程中可增长，provider 必须按实际字节限额；最终 size/hash 来自冻结的本地文件。Docker 直接用文件描述符接 exec 流，不增加 tar 打包；get 用 limit+1 检测超限，put 原子替换。
- RSS 与cgroup内存不是同一指标：双路100MiB扩限压力的RSS约68MiB，但文件页缓存触发384MiB cgroup reclaim（max事件，无OOM）；不能用低RSS声称内存还有同量余量。压测必须保持真实sandbox1536MiB预算：早期夹具误设256MiB，在50MiB不可压缩快照上传时sandbox本身OOM，不能误归为Broker OOM。
- 并发探针须先 manager.recover，与HTTP lifespan一致；直接并发首次create会正确碰到 recovering 准入拒绝，不能当正常流式并发故障。真实测量边界与原始曲线见本轮 Broker streaming 审查记录。

## 2026-10-23 资料库连续表格

- 列表的表头与正文必须共享同一个 table 布局；分别使用 flex 且给每行圆角会失去连续分隔与列对齐。用真实 thead/tbody、固定辅助列宽、每个单元格的下边框，不给文件名单元格设 overflow:hidden（会裁掉弹出菜单）。
- 窄屏隐藏独立大小/时间/项目列时，需在文件名下保留信息与项目链接；长文件名/项目名都截断，保留 title。项目详情中不重复展示所属项目列。
- hover 隐藏操作不能只让最内层按钮显现；祖先 opacity:0 会让键盘 focus 仍不可见。文件行 focus-within 和展开菜单状态也必须显示操作区。
- 动态切换主题后直接截屏可能拍到 CSS transition 的中间色；E2E 截图使用 animations="disabled"，手机截图先关闭侧栏遮罩。表格真实几何与交互回归见 `platform/e2e/test_library_ui.py::test_library_table_keeps_columns_and_row_boundaries`，验证入口见 `docs/progress/updates/2026-10-23-library-table.md`。

## 2026-10-23 流式缓冲与模型容量

- “每隔250ms刷新”不能只在 on_text/on_reasoning 收到下一块时检查时钟：短叙述后模型可能连续数秒只生成隐藏的工具参数。必须有独立定时刷新，并在正常结束、异常、取消时刷新尾部；刷新与回调共享锁，刷新任务归属于单次模型调用，终止时取消并等待回收。空缓冲不得触发 API/DB 写入。
- 模型目录的 context_tokens/max_output_tokens 必须按实际模型能力配置。隐藏的全局8192上限会让更大的目录值失效；全局上限现在仅在运维显式配置时生效。预留额度与上游 max_tokens 必须一致，输出截断不能执行残缺工具或盲目重试。
- E2E 的流式等待触发词只匹配完整用户输入，不能包含匹配：自动标题请求会带上原始内容，误触发测试闸门会导致标题超时，掩盖已经成功到达页面的叙述。浏览器回归先验证完整句子可见，再显式放行模拟模型，不用固定sleep假装流式验收。
- 提高最大输出不会预先分配全部内存，也不等于实际一定输出这么多；但会增加账本的在途预留，以及真的长输出的时间/内存。保留用户额度、单Run预算、超时与checkpoint上限，不把短文本合成探针当1M上下文/384K生成的容量验收。

## 2026-10-23 对话删除与沙箱准入审查

- 删除对话把Run改为canceled不等于同步回收沙箱。Agent的 `_acquire_lease` 等待Broker准入/创建HTTP期间不检查取消，Broker准入后也未重查Run状态；探针确认取消后仍可拿到租约。后续finally/租约可兜底，但不能承诺2秒内回收。修复须贯通两端，不能只取消客户端HTTP而留下服务端创建。该旧审查已由本轮取消贯通修复替代，最终证据见 `docs/reviews/2026-10-23-library-runtime-acceptance.md`。
- 旧审查中的Docker独立TTL缺口已由可信PID1监督与--rm移除兜底修复。正常释放、失败重试、进程故障后的最终收敛与物理清除期限应分开描述。对话删除保留资料库产物，工作区/断点/软删历史分别有保留期。


## 2026-10-23 命名空间与真实回收竞态

- 项目/文件重名用规范化哈希作唯一键；移动、恢复、新版本、产物和预签名必须共用分配逻辑。SQLite表达式索引的coalesce空字符串要固定SQL字面量；绑定参数不会匹配该表达式索引。
- 技能文件用途必须在上传时指定，Worker识别历史技能时也迁移整条版本链；仅在UI隐藏不够，普通附件输入仍须在API拒绝。无效/pending/rejected技能也必须能在技能页管理和删除。
- READY的文本产物不一定经过Worker提取，不能永久缓存text_unavailable；派生文本在受限Worker内提取，不能在API直接解析不可信文件。
- provider列表是历史快照，可能读取到启动中的running=false，而持有stripe后租约已经active。删除前必须重新inspect确认当前停止/不存在；不能因旧列表误删正在执行的render沙箱。真实联合负载复现了此问题，单独mock绿不能替代组合验收。
- 并发跑冷启动Keycloak、构建、E2E和双库性能断言会污染小机时延。一次PG500轮首屏p95=199ms，隔离复验64.65ms；保留失败证据，按实际隔离负载重新测量，不放宽150ms标准。
- 实际Agent SIGKILL恢复同时受持久claim和Redis pending可见性期限约束。只加速数据库时间不会让旧transport立即可见；加速验收必须明确两者，不声称生产配置立即接管。

- 思考模型的max_tokens包含reasoning，自动标题的40字约束不能直接换算成64输出token；应使用既有后台调用2048预算并在显示时截断。真实主回复成功而标题64-token调用length，是单独的预算错误；保留独立账本身份，标题失败不能丢正文。

## 2026-10-24 连续预览与工作时长

- 全屏和聊天侧页面预览只用一个原生滚动组件；不要给wheel绑“下一页”操作，触控板惯性会误翻多页。分页按钮/缩略图应滚动到实际页面，滚动容器contain防止传给外层。
- 工作时长由API状态CAS持久结算running区间；用户等待/暂停/业务排队不计入，running内的模型/工具/沙箱等待计入。旧行未知净时长不估算；前端不按Date.now重新累计，不新增每秒刷新任务。
- 过程条目的CSS类必须加前缀（如process-ask），不能复用内层Ask的`.ask`，否则重复样式与可访问定位冲突。已展开按钮的文本包含折叠符号和布局换行，计时稳定性应比较其实际状态文本span，不能把整个按钮innerText当时长。
- PostgreSQL容器初始化时临时server的Unix socket可能先就绪；验收库创建前应检查TCP主server就绪，再确认目标库存在。不能让一次createdb失败后继续全套测试，造成无关的成批迁移错误。
- 纯UI滚动验收使用确定性渲染页，经真实授权预览端点读取；宿主Office/字体是否可用另有集成测试。不可用的PPT渲染不能被误判成滚动组件缺页。
- 本机（Mac）原来留着的 PG15511 测试库随旧 Colima VM（`~/.colima`）于 2026-10-24 删除，数据为一次性测试数据。需要时重建：`postgres:16`，`POSTGRES_USER=ailab POSTGRES_DB=ailab_test POSTGRES_HOST_AUTH_METHOD=trust`，监听 `127.0.0.1:15511`；Redis16511 同理按需新建。重建后先检查 TCP 主 server 就绪再跑全套测试。详情见 `~/Documents/AILab-local/HANDOFF.MD`。

## 2026-10-24 受控联网

- 先解析检查再交给 httpx 连接是 TOCTOU：httpx 会再次解析，DNS 重绑定可指向内网。必须用自定义 httpcore 网络后端解析一次、检查全部答案、直连检查过的 IP（TLS 仍按主机名校验）。`ipaddress.is_global` 把 NAT64 `64:ff9b::/96` 判为全局，需另查内嵌 IPv4（映射/NAT64/6to4/Teredo）。外部请求客户端一律 `trust_env=False`，图片下载原来就漏了这一点。
- 内部 API 的 409 专指 attempt 失去 Run（fencing），Agent 会直接中止 Run。业务冲突（如同名技能）必须用 422/404，否则一次正常拒绝会变成 “agent crashed unexpectedly”。
- Chat 一旦提供任何工具（如 save_skill），原来“tools 为空就忽略调用”的保护就失效；必须按本轮实际提供的工具名决定是否执行，否则模型发来的 write_file 会在 Chat 里起沙箱。
- 镜像只装运行时依赖：模块级导入只在 dev 环境里间接存在的包（如 packaging）测试全绿、上线即启动失败。`tests/test_runtime_dependencies.py` 检查这一点；新增第三方导入先声明到 pyproject 并跑 `lock_deps.sh --check`。
- runsc 根 overlay 默认 `root:self` 由宿主文件承载、没有大小上限，`--memory` 不限制磁盘（512 MiB 内存下写入 900 MiB 成功）。用 `--overlay2=root:self,size=4g` 作为 Docker runtimeArgs；在 Colima 上要写进 profile 的 `docker.runtimes`，只改 VM 内 daemon.json 会被重启覆盖。改 daemon.json 后 `kill -HUP dockerd` 即可让新容器生效，不必重启服务。
- `tarfile` 的 data filter 会去掉前导 `/`（绝对名落在目标目录内，不报错），但对 `..` 和绝对链接抛异常；解压脚本要在 finally 里清理部分目录，否则拒绝后留下半个仓库。
- URL 来源提取的正则只取 ASCII URL 字符；否则“链接。”里的中文句号会并进 URL，导致用户给的链接被判为未授权。
- 离线演示（MockLLM）原以“tools 是否为空”区分 Chat/Work；Chat 有了 save_skill 后它会去调沙箱工具。Mock 只能按本轮实际提供的工具名决定脚本。E2E 依赖 Redis（默认 6379），本机没有时用 `AILAB_TEST_REDIS_URL` 指向一次性实例，否则整套报 broker did not come up。

## 2026-10-24 k3s / OpenSandbox 受控联网

- `sandbox_disk_bytes` 是业务预算，不是节点磁盘隔离。k3s containerd 要经 `ConfigPath` 读取 `[runsc_config] overlay2="root:self,size=4g"`；`bash k3s/install-runsc.sh` 安装、节点维护窗口重启 k3s，再跑 `k3s/check-node.py`。1.34.3 的默认配置没有自动 import 目录，本轮最小 `config-v3.toml.tmpl`（只渲染一次 base）已实测。不能复制整份生成配置再追加 base，旧“所有 template 都不可用”的记录过宽。自定义 template 不覆盖。
- namespace LimitRange 5Gi ephemeral-storage 依赖 kubelet 驱逐，不能替代同步根overlay上限；mounted writable volume 也不在根overlay限制里。本轮 uid10001、512Mi内存的Pod实际写到4294967296字节后ENOSPC，不能只用statvfs声称写入限额已验证。
- OSB Server/SDK 1.1.1 明确拒绝 gVisor + sidecar networkPolicy。默认sidecar不改；只能显式选 Kubernetes 模式并先验证 namespace/CNI/节点规则。secure 还要求隔离验收声明（不是持续证明）。健康端点不能证明server跑Kubernetes，`osb_up.sh`现在拒绝复用Docker模式配置，并核对固定namespace/runtime/template。
- kube-router 默认放行 ICMP，gVisor无CAP_NET_RAW仍可用SOCK_DGRAM ping socket。`net-raw=false`、drop ALL及ping_group_range都不足以断绝该出口；节点按固定IPv4 Pod CIDR补raw PREROUTING规则并由systemd持久化。本轮移除规则时ICMP契约红、恢复后绿；该规则也关闭其他Pod的诊断ping。换CIDR/双栈/CNI/节点拓扑必须重验，lite preflight拒绝不匹配CIDR。
- OSB注入文件前先在既有exec预检里以执行用户mkdir父目录，再交SDK上传；否则execd可创建错误属主目录。真实传输契约必须使用全新多层目录，并在其中写入/解压，不能只测已有outputs。
- 新版gVisor必须安装整个发行包（runsc、shim、同目录gvisor-bin/），只复制两个二进制会在创建时缺gvisor_sentry失败。下载发行包并校验官方SHA512，见gVisor安装文档。
- Colima自定义COLIMA_HOME必须先mkdir；目录不存在会悄悄回退默认目录。`--runtime docker --kubernetes`默认让k3s使用`--docker`，这不能验证containerd配置。独立临时VM里移除k3s unit的`--docker`再daemon-reload/restart，或从一开始选containerd。绝不能在共用部署VM上照搬。
- 复验入口见 `platform/tests/contracts/README.md` 和部署README：真实传输/生命周期、公开下载与离线安装、恶意归档清理、TCP/UDP/ICMP边界。TCP/UDP要先从节点验证目标正在监听；“目标本来不可达”不算隔离证据。save_skill契约只验证真实沙箱取回文件和API调用载荷，API持久化/扫描由独立测试覆盖，不能混称五服务端到端。

## 2026-10-24 CI / 测试完整性

- `docs/` 不等于文档：历史验收目录有 Python/Shell/Dockerfile。CI 只对白名单 Markdown 省测，未知文件保守全套；重命名关闭检测以保留双端路径，始终比较完整 PR base...head。
- GitHub job success 不证明真实契约执行：专门验证 JUnit 中匹配的 Redis/PG/S3/重型组件用例，拒绝无报告、无匹配或跳过；S3 的 local inode 不适用项单独白名单。新增 job 必须同步 `ci_gate.py`，门禁严格校验完整集合。
- 脱敏回归不要把 `private` 等普通单词在整个配置中全文禁用，macOS 正常 `/private/` 路径会误报。应逐项断言敏感字段和 URL 的预期脱敏值，同时保留合法路径。
- E2E 的 npm test/build 输出须流式留在 `e2e-logs/`，失败 trace/截图要用一次真实故障探针验保存；不能只凭 hook 代码声称诊断生效。就绪必须是 200，404 不是服务可用；kill 后还要 wait 回收。
- 独立生产依赖 venv 里的服务导入才证明 dev extras 没有掩盖启动缺包。重型 Docker/Office/ClamAV/Keycloak 每周/手动验收与每 PR 流水线分开；工作流静态通过不等于真实组件或远端 Actions 已通过。

## 2026-10-24 用户记忆 / 技能在线编辑

- 账号记忆与技能开关共存 `users.settings`；必须在同一个用户锁内 refresh 后写新 JSON，不能从认证时的旧对象覆盖整份 settings。记忆 body 的 expected_revision 和技能的 expected_file_id 防止前端与 Agent 相互覆盖；内部业务冲突仍用422，409只表示失去 attempt。
- 记忆工具重投必须在同一事务写 RunDelivery；prompt 是每轮冻结快照，工具 list 才是实时状态。关闭自动记忆与关闭使用记忆是两种语义；用户页面的编辑不被后台同步静默覆盖。模型口头“记住了”不是持久写入证据。
- ZIP技能带脚本/素材，不能把说明另存 Markdown 覆盖整个包。公共 parent_id 上传原本可从旧版本分支，技能更新须额外检查当前head/非pending并锁内分配。个人Markdown编辑复用100KiB正文上限、扫描/审核与版本历史，不在API解包ZIP。
- 浏览器保存后不能只看“旧行编辑按钮仍enabled”就刷新：先拿成功POST的新file_id、等弹窗关闭，再确认该新文件扫描终态或页面已展示新head。只检查旧DOM可能中止页面扫描等待并把pending行误当最终结果。
- Hermes参考与本轮验收见 `docs/reviews/2026-10-24-memory-skills.md`。当前是有界账号记忆和前台模型工具，不等于向量召回、后台反思、付费模型质量或生产容量验收。

## 2026-10-24 对话能力边界

- 对话是正常助手，仅不提供沙箱执行/文件交付工具。不要先注入工作模式的全套沙箱指令再追加“只对话”，也不要把写代码/脚本/SQL草稿等同于实际执行。只在确实需要沙箱操作时解释具体限制，不逐轮宣告模式。
- 模式选项保留在输入器即可；消息元信息只展示实际技能和附件，普通chat不添加模式chip。界面说明不能代替工具白名单：搜索/读网页/技能/记忆允许，沙箱工具仍由Agent实际白名单拒绝。

## 2026-10-24 Fork / 重试 / 资料库工具 / 压缩阈值

- “任意轮次分叉”和“重试前恢复”要求保留历史工作区；不要在后轮完成时删前轮快照。当前保留至既有workspace TTL；失败/取消轮次也可能有runtime_meta.workspace快照。源对象过期/缺失时Work fork必须422，不能静默换用错误工作区。克隆对象使用新Run自有key和一致保存时间，不能仅重写数据库指针。
- Fork不复制账本/任务，只复制显示usage、事件和预览，Artifact复用file_id；否则会重新执行或重复计费/资料库字节。DB与对象存储不能原子提交，复制失败须回滚DB并清理新key，硬退出仍靠既有GC收敛。复制过程必须有数量、字节、时间上限。
- Retry必须在同一user锁和事务内复用start_run检查、创建outbox并标记旧Run已替代；不能把一个会自行commit的start_run嵌进外层事务。summary边界若正是被替代轮次，要清空摘要并重建；否则旧回答仍会漏进模型上下文。skill隐含work时，队列topic也须按最终options.mode生成。
- Chat已提供资料库搜索/阅读；旧测试精确工具白名单需要同步，不能为让旧白名单测试绿而移除新工具。资料库原文件由Broker拿API核准的Run grant流式注入，不能让Agent整份缓冲，也不能给模型传user_id作为归属依据。
- Admin审计原来只识别GET/POST/PATCH/DELETE；新增PUT路由须同步审计method映射，否则正常403/422会变KeyError/500。设置按bootstrap一次主键读取，无需每步查询或无失效机制的进程缓存。
- 自动压缩的软阈值在完整装配后的历史上应用；硬溢出仍增量处理。不能对每追加一条历史就触发低阈值摘要：固定系统prompt已经超过软阈值时，会造成逐轮重复付费摘要。没有待压历史时软阈值不是拒绝当前问题的硬上限。

- PostgreSQL的is_head是Integer，不能写`IS true`；用`= 1`，同时满足SQLite/PG和已有部分索引条件。此次SQLite绿而PG红已用真实双库捕获并修复。
- 并发标题须先等正文首个额度预留成功再做自身预留（不等正文生成）；否则可选标题可能在临界余额下抢走主回答额度。标题的生成超时与取消后账本清理是两段时间，不把10秒当整个job清理SLA。

## 2026-10-24 Portal 打磨 / 思考展示 / 分叉重试

- 应用内链接必须用 `next/link`；`<a href="/…">` 是整页导航，切会话会白屏重载。`/c/[id]` 用 `key={id}` 重挂载 Chat，靠 `lib/conversation-cache.ts` 即时显示；新会话发送后先写缓存再 `router.push`，问题立即可见。
- 正文 = 模型最后一次动作之后的文本（`splitTurn`）。“动作”只算思考/计划/提问/带 `modelStep` 的工具步骤；预览/归档等平台提示不算，否则正文在答完那刻被拉回过程框。E2E 取答案用 `.assistant-body > .md`，过程里的思考也渲染 `.md`。
- 收起内容用 `.collapse`（grid 0fr→1fr）始终在 DOM 中；关闭时子元素 `visibility: hidden` + `inert`，否则 Playwright 视其可见、键盘可聚焦。`.live` 是旧的实时指示类（inline-flex+圆点），新组件不能复用此类名。
- 依赖 localStorage 的初始布局（侧栏收起）要在首帧前应用：根布局内联脚本写 `html[data-sidebar]`，`.shell.settling` 期间禁止过渡；否则刷新后侧栏先出现再淡出，E2E `is_visible` 也会抓到中间态。
- iOS：输入框 <16px 会在聚焦时缩放且不回弹，用户看到“能左右滑、键盘收起后不回来”。手机端统一 16px；键盘只缩 visual viewport，聊天需固定到 `--app-h/--app-top`（`useVisibleViewport`）。无头 Chromium 无法复现键盘，真机仍需验。
- 完成后自动打开预览只在“本页跟随的 turn 有 preview.updated 且终态 succeeded”时触发一次，且仅宽屏；不要在 `preview.updated` 时打开（用户明确反对）。E2E 中电子表格/PPT 用例据此改为等待自动打开。

- Round 2 标题生命周期：不能在正文终态的finally无条件cancel标题。先提交正文终态，再用原任务剩余预算收尾；保留durable attempt及HTTP client直到收尾/取消，再ACK。shutdown/attempt丢失必须取消子模型并await、结算、清理heartbeat；只去掉cancel会留下无主任务。Round2起标题10秒从主额度gate通过后计时，不从正文结束重置；gate不抢主额度，受run截止/取消约束；正文未预留即失败时取消gate任务。账本取消清理仍受既有HTTP重试边界，不冒称严格10秒job释放SLA。
- `run.completed`不是`stream.end`：终态后空轮询会关SSE，关闭时列表刷新也可能早于晚到标题。空标题且有效attempt时保留最多终态后10秒的SSE窗口；先检查pending再读取事件，避免CAS刚提交时看见非空标题却漏读同事务事件。cancel/delete不留窗口；ACK/过期立即收敛。API标题写入仍走原fence与空标题CAS，取消Run不能补标题。
- 自动标题的Markdown清洗必须在一个无ORM依赖的纯文本helper统一，Agent/内部endpoint/result fallback复用；先去链接/强调/代码/heading、折叠空白，再截40字。原始请求上限和最终存储上限不同；保留40字请求上限会在清洗前422，不能验证/解决长Markdown输入。

## 2026-10-24 会话树 / 搜索 / composer用量

- 树形会话没有“时间之前就是历史”的语义；模型history/摘要through/workspace/预览/fork/隐含附件与产物版本父节点都必须按Run祖先读取。superseded_by_run_id只剩审计用途，不能继续作为隐藏过滤。Run直接入库的测试数据要同时设置parent_run_id和conversation.active_run_id；不要在业务读取时增加时间排序fallback掩盖缺失关系。
- 递归CTE命中索引不等于单层O(log N)：SQLite可把join变成每层扫conversation索引；PG可把相关EXISTS去相关成会话半连接，500轮首屏曾p95 165.18ms失败。当前按id标量取parent和其conversation，边界500层，双库EXPLAIN与分页延迟须一起验。
- conversation.summary是可替换缓存；每次模型读取检查through_run_id是该Run祖先，失效则空摘要+从祖先重算。CAS要比较数据库实际缓存边界，同时校验客户端看到的有效边界，才能覆盖另一分支的失效缓存，而非永久冲突或泄漏旧答案。
- 搜索是有界扫描而非全文索引，每请求最多50会话/1000 Run候选；空items加next_cursor仍须续查，直到null。SQLite原生lower只识别ASCII，搜索用专用Unicode lower函数；snippet/highlight按Unicode code point，Portal不能直接把带emoji的偏移当UTF-16下标。查询长于120字符时仍要高亮可见命中部分。
- 旧账本没记录quiet用途，不能从output上限/顺序猜正文调用；0023新增nullable quiet并在reservation冻结、ledger结算沿用，历史unknown的composer计量返回null。标题/压缩结算晚于正文也不应覆盖主调用表值。
- 迁移测试降到中间版本后，不要用最新ORM查询缺失的新列：原0018账本往返测试已改为升级head后做ORM断言。大树测试会让全库超过1000行，对账测试必须消费next_run_id，不能假设第一批包含自己的随机Run。

## 2026-10-24 定时任务 / 资料库页码引用

- 定时执行不能直接 enqueue 或调用会自行 commit 的 start_run：用户锁内 CAS next_run_at，并用 start_run(locked=True, commit=False)，claim/新会话/Run/outbox 同事务。只将可预期业务错误回滚至 savepoint 并记录失败；基础设施异常让整个事务回滚。项目删除清空 project_id 且停用任务，避免下一轮在错误上下文静默执行。
- zoneinfo 给不存在的墙钟时间也会构造 datetime；须 UTC roundtrip 检查，秋季重复时间固定 fold=0。每周恰逢缺失时间可能要等14天，不能只扫描未来7天。PG可能返回宿主时区 aware datetime，不能用 replace(tzinfo=UTC) 做转换；只给SQLite naive补UTC，aware用 astimezone。
- Worker显式页标记必须包含第一页，旧无标记缓存不能默认为第一页；DOCX段落/Excel sheet不能冒充渲染页。内部 bootstrap 返回的新字段还必须加入 InternalRunDetail，否则 FastAPI 会静默过滤，handler单测无法发现。
- 此子任务0024临时接0022（没有0023 stub）；父Agent合入会话树0023后必须reparent为0023并做双库迁移回归。接口、测试、成本和未验边界见 [后端记录](docs/progress/updates/2026-10-24-schedules-citations.md)。

## 2026-10-24 合并功能测试

- 独立迁移分支合并后，中间revision的结构测试可保持原目标，但 `compare_metadata(..., Base.metadata)` 必须先升级集成head；最新ORM包含后续表时，在0023直接比较会把合法0024误判为漂移。完整线性链另用0021→0022→0023→0024逐步升/降/再升锁定，不能删掉漂移断言。
- 不要把人工持有stack的辅助（如曾误提交的 `e2e/test_zz_hold.py`，默认持有5400秒）放进 `e2e/`：全套会被它卡住，已删除；人工浏览器检查用HANDOFF里的Playwright路线临时起stack。旧测试种子也必须遵循会话树关系，不能只按创建时间写Run（见前面会话树章节）。
- 分支契约已统一为“截至所选轮次的祖先前缀”：前缀里有Work轮次只能分支为Work，前缀全是Chat可分支为Chat或Work（用户需求“工作只能分支为工作”指工作轮次；更早的纯对话轮次分出的新会话不含任何工作内容）。API、Portal与E2E同此语义；E2E同时断言工作轮次只给Work、之前的对话轮次两者都给。首轮及最终证据入口见[集成测试记录](docs/progress/updates/2026-10-24-integration-tests.md)。


## 2026-10-24 Round2 发布验收陷阱

- PostgreSQL UTF8不等于Unicode大小写转换：initdb C locale的lower('ÉCOLE')仍为'École'。搜索窗口已显式使用ICU root `und-x-icu`，PG16部署须包含此collation；双库EXPLAIN与C locale回归均应保留。不能通过切换测试locale或skip掩盖契约失败。
- 新路由的composer可先出现、model选项稍后加载；浏览器探针应等发送按钮enabled并确认POST被接收，不能只按Enter后把未创建Run误当模型卡住。文件页码引用渲染为`.md-file`按钮，非a；用原始Markdown校验ailab-file与page，再点按钮测查看器几何。
- 真实低阈值压缩可用临时catalog收紧合法context预算，管理员threshold_ratio=0.2；必须记录此缩小窗口与真实provider调用边界，不能写成默认百万窗口长期容量证明。个人catalog与开发.env不变。


## 2026-10-24 合并main后的部署验收

- 部署固定merge SHA，镜像内Python及Portal源码逐文件比对，不能只用镜像tag推断内容。个人catalog挂载会覆盖仓库默认配置；quiet直接回答能力需在个人catalog显式配置，不能假设代码升级就启用。
- 本机编排历史资源上限可能高于仓库manifest；验收须核对实际限制并采集CPU/RSS。Docker MemUsage不是进程RSS；临时docker exec观察进程本身会抬高RSS，必须剔除或明确作为保守上界。模型主Run结束后标题/预览仍可能短暂工作，不把刚终态的采样称为长期空闲。
- 备份不仅复制DB：停写后完整数据卷归档，checksum再读校验、实际恢复读取SQLite并核对每个ready对象的hash。迁移后先做严格ID/数量比较，再执行smoke；smoke之后比较旧ID保留，不能把自己新增测试数据误报迁移丢失/增生。
- 搜索标题命中不承诺正文mark；浏览器应断言可选结果和导航。会话列表接口返回数组，不能使用旧items包装。清理须限定专用测试账号，按公开软删除契约保留审计数据；删除会话不会删除资料库产物。
- 定时任务“立即运行”不能证明后台dispatcher运行；另安排一次真实未来到期，记录相对到期时间的延迟并删除任务。空队列CPU探针只是每轮成本估算，不是任务执行负载。监控须使用ops凭据，旧service token的403不能通过放宽认证处理。
- PDF浏览器smoke区分print视图/print调用和系统保存文件；拦截window.print便于自动化时必须记录边界。内置preview超时按既有两次失败规则回退，不能把工具超时当产品错误。
- 本次部署和详细证据见[Gate 3发布记录](docs/progress/updates/2026-10-24-release-ui-features.md#gate-3部署合并后的-mainround-3)；个人备份/配置/地址/账号/原始日志只记仓库外本机HANDOFF。

## 2026-10-24 项目说明 / 品牌后端

- 品牌候选不能直接覆盖有效设置指针：先扫描，再在受限子进程分析，READY 与设置激活同事务。品牌更新/Worker激活/首轮快照共用平台设置锁；运行快照写入还要取得运行所有者锁，避免覆盖 checkpoint 的 runtime_meta。不要在持有用户锁时反向调用首轮品牌 bootstrap。
- `InternalRunDetail` 必须显式声明新 bootstrap 字段，否则旧 GET 会被 response_model 静默过滤。Worker 文件 DTO 没有 purpose 字段，品牌分析分派依据管理员设置的 `meta.brand_kind`，不要只在 ORM 上设置 purpose 就认为 Worker 会进入新分支。
- 品牌资产不等于资料库文件：单文件与批量 owner API、配额计数、库列表/工具及 Broker 的普通 copy 路径都要检查。Broker 品牌读取必须绑定 Work Run 的冻结资产集合及 `job=brand`；恢复工作区后重置 `.brand` 再注入，禁止旧快照覆盖本轮配置。
- 技能目录哈希包括所有文件。测试直接 import 镜像内 build.py 会写入 `__pycache__` 并导致技能校验失败；导入临时副本，修改 SKILL.md/build.py 后同步重新生成 `images/sbx-office/skills/index.json`。本轮需要重建 sbx-office 镜像，未重建/部署不能称运行镜像已有品牌支持。
- 老集成迁移测试固定 head=0024，新增0025必须一起扩展逐步升/降/再升链与 head 断言，保留漂移校验。父任务合入0026/0027时继续扩展；不能只改生产 migration 后跳过旧测试。
- SVG拒绝、旧品牌物理资产留存与API候选显示约定见[契约修改](docs/reviews/2026-10-24-workspace-features-design.md#a-分支对契约的修改)。旧品牌尚无自动回收，计入平台磁盘而非个人配额；不要用普通库删除接口清理仍可能被 checkpoint 引用的资产。

## 2026-10-24 Inbox / Queue / Steer 后端 B

- 统一 `transition_run` 若锁后refresh，必须先flush调用方pending ORM变更；否则ask_user刚写的question/runtime_meta被丢弃。用户锁须可重入，既有调用方已持同一锁；PG仍靠用户行锁持到事务结束。
- Steer claim不是注入确认：已领取未checkpoint消息须在新attempt重新返回；ID、messages与agent.steered同checkpoint事务确认。领取时直接发事件或只返回queued，会在claim后进程死掉时丢失提示。等待通知的永久去重不能依赖会被200条裁剪的notification行。
- Agent真实历史来自分页model-context，不只legacy internal GET；两处都要补steers，history_messages在原prompt后按注入顺序插入。额外状态轮询会改变取消时序/把瞬态故障变终态；复用原状态心跳字段，不加每步HTTP请求。
- 分叉需复制已确认steer历史并重映射事件queued_id（待处理queue不复制）；新增queue会话外键要求GC先按批清queue，不能直接删除会话。
- 终态派发用savepoint区分预期准入失败（暂停并保留队列）与基础设施失败（整个终态事务回滚）。会话通过项目删除也必须取消queue。0026在B临时接0024，父任务改链并同步集成head断言；见[B记录](docs/progress/updates/2026-10-24-inbox-queue-backend.md)。

## 2026-10-24 C 分支：内部分享 / 资料库摘要

- 分享的 file/conversation 外键使用 ON DELETE SET NULL；否则软删除后的永久清理会被分享记录挡住。授权必须同时检查对象存活、所有者活跃、受众和固定 through_run_id 路径；不能用当前活动路径替代快照，不能把输入附件当产物授权。
- 分享复用文件流/ETag接口后须覆盖 immutable 缓存为 private/no-store；分享 DTO 的文件地址走 `/api/v1/shares/...`，不能复用所有者 download_url 或对象存储签名URL。
- 摘要须扩展完整 JobEnvelope→claim→Worker→API→业务结果→ACK 链路，并和 READY/OCR完成同事务 stage_job；明显无文本的文件直接 skipped，避免每个图片都产生空作业。OCR晚到只重新排队一次。
- 不可变账本里的已结算 call_id 不能重新用于一次新推理。首次 `summary:<file_id>`；明确失败重试使用独立后缀（最多3次总调用），不得通过删除旧账本实现重试。通知接缝 `api/share_notifications.py` 当前 no-op，父 Agent 接 B helper。
- Worker 派发标志不能写回原队列消息的 payload；内存队列会把同一对象重投，extra=forbid 的 JobEnvelope 将拒绝被污染的消息。
- read_library_file 输入 pages=[2] 与输出 pages=[{page,offset}] 不同；扫描PDF OCR必须保持原物理页码。两页测试客户端也必须提供两页预览，否则 page_limit 是预览样本不完整，并非OCR丢页。验证与未验边界见[本轮记录](docs/progress/updates/2026-10-24-shares-library-backend.md)。

## 2026-10-24 工作区集成验收

- 接收者通知会取得接收者用户锁；分享不能先锁所有者、写外键后再逐个锁接收者。须在写入前按全局用户ID顺序取得所有者与指定接收者锁，通知仍同事务插入/裁剪。跨进程PG互相分享用例覆盖真实数据库锁，不能只测单进程RLock。
- 测试子进程必须显式指定本工作树的platform cwd；复用别处venv时，editable install可能加载主工作树旧代码。不能把其他代码基线的失败算作当前分支失败，也不能把其他分支的绿算作验收。
- 已claim的steer还不是已注入；详情仅按runtime_meta.steer_ids确认顺序查有界队列表。重新加载的bubble不能从所有steered行猜测。队列scripted model应识别当前最新原始用户消息，不能把history里旧的慢任务当成新任务再次阻塞。
- E2E通过API准备项目后须重新加载以初始化前端项目缓存；按文件ID核对移动后名称，重名会合法改名。手机证据先收起桌面遗留侧栏，避免截图只显示遮罩。
- 品牌保存空字体用空字符串（非null）；产物菜单提问/移动必须读取文件当前项目，不能沿用对话项目。两项Portal回归由父Agent修复，未放宽浏览器断言。

## 2026-10-24 Portal 动画 / 原生弹窗
- 删除旧样式块时连带删掉了 `@keyframes spin`（b075489），所有转圈（预览、上传、忙碌按钮）都停在一帧，看起来像卡死；后端预览其实几秒就绪。现在 `tests/css-animations.test.mjs` 检查 globals.css 里用到的每个动画都有 keyframes。排查“卡住”先看 `el.getAnimations()` 是否为空，再查后端。
- `prefers-reduced-motion` 的全局规则把动画压成 1 次；进度类转圈（`.viewer-spinner`/`.chip-spin`/`.pr-spin` 等）已豁免为慢速持续转，新增转圈类要加进同一行。
- 不用浏览器 `confirm()/alert()`、原生 `<select>`、`type=time/color`：确认用 `lib/dialog.ts` 的 `confirmDialog()`（根布局挂 `DialogHost`，Escape 在捕获阶段拦截，不会连带关掉下面的对话框）；下拉用 `components/select.tsx`（列表 portal 到 body，不被弹窗滚动区裁切），时间用 `TimeSelect`。
## 2026-10-24 PPT 模板

- POTX 的 package main content type 不同于PPTX，python-pptx `Presentation(path)`会拒绝。Worker应注册模板main part直接打开；不要为30MiB输入重打包到受16MiB输出限制的解析目录。生成器在沙箱内规范化后再用Presentation。
- `.potx`也必须走ZIP大小/压缩比检查。模板状态 failed 与扫描 rejected 由meta区分；FileObject仍沿用原扫描状态枚举。
- 模板Run冻结在 `options.template`，队列/重试/编辑复制冻结值，不再重新解析可删除注册项。Broker必须restore后清 `.template`，连无模板轮也要清。
- 数据迁移若需补outbox，topic/deadline须取有效Settings，不能只读os.environ：生产自定义队列可能来自env_file，否则迁移任务会发到无人消费的默认队列。0028双库回归锁定自定义topic。
- 测试服务必须核对进程归属，不能只看daemonize退出码或端口可连。macOS可能同时存在wildcard与127.0.0.1特定绑定的Redis监听；启动前检查端口独占，启动后核对pidfile与Redis INFO的process_id、验证空DB，清理时只关闭自己持有的进程。本轮最终重验改用独占26379，原16379非本任务进程保持不动。

## 2026-10-24 PPT 真实首屏图跟进

- **Office rendering happens in the sandbox preview pipeline, never in the platform image/Worker.** Worker预算256–384MiB，旧方案在256MiB下首屏245MiB、默认192MiB必然fallback，已被否决并撤销。不得通过平台镜像安装LO或放宽Worker隔离来重新引入它。
- 模板ready、preview manifest和outbox同事务；复用资料库请求helper和原preview作业，不在补丁重试或ACK后重建作业。template status独立于preview，失败只保留草图，不影响Run冻结和使用。
- immutable草图切真实图时必须改变thumbnail_url及ETag；仅替换同URL字节会让浏览器继续显示草图。API从完成资产中选择文件绑定的固定缩略图路径，模板可见性不借用普通资料库授权。
- 真实渲染宿主进程测试仅证明renderer/租约生命周期与内容，不等于OpenSandbox容器/cgroup验收；当前证据及边界见[记录](docs/progress/updates/2026-10-24-ppt-template-thumbnail-rendering.md)。


## 2026-10-24 集成后的 E2E 控件契约

- Portal 模型选择现为“模型”按钮/listbox，而非原生select；必须实际点击选项并验证选中与刷新持久化。个人技能在“更多技能”menuitemradio，不保证出现在快捷chip。行政策略/定时模式同样使用页面listbox。
- 删除等确认改为页面alertdialog，不能再监听浏览器dialog；明确点击后验证成功/失败原契约。记忆冲突“重新加载”保留草稿并无确认，不要给无确认动作添加等待。
- 完整集成E2E必须覆盖这些变化，不能因Portal单测/build通过就沿用旧浏览器计数；本轮最终88通过及初始失败见[记录](docs/progress/updates/2026-10-24-ppt-templates-clean-acceptance.md)。


## 2026-10-24 模板 ready 与 preview 完成是两个时刻

- ready表示模板已扫描/分析、可用于Run，不代表sandbox首屏预览已完成。UI只轮询pending会停在草图；真实图已经改变URL/ETag也无法更新旧组件状态。公司管理列表和个人模板菜单均已实测复现。不能在后端延迟ready/放宽Worker限制来掩盖问题，应跟踪独立preview生命周期，见[部署缺陷复现](docs/progress/updates/2026-10-24-ppt-templates-clean-acceptance.md#已确认的-portal-缺陷真实缩略图不自动替换草图)。
- 文件系统校验成功仅证明结构可挂载，不证明运行时镜像文件内容完整。存储中断后须逐文件hash与实际执行验证；镜像标签、架构一致或pull成功都不足以证明内容未损坏。个人部署的恢复/清理命令只写仓库外交接。

## 2026-10-24 模板预览UI跟踪

- 模板API的 `preview_status` 独立于模板 `status`；ready模板可立即选择，preview pending继续跟踪至终态，不通过thumbnail_url格式猜测状态。关闭个人菜单仅保留所选pending模板的可用性检查。轮询取消/超时/截止与失败保留草图见[修复记录](docs/progress/updates/2026-10-24-template-preview-refresh.md)。
- E2E的health探针不能证明进程归属：SSH转发也可能占用测试端口并返回healthy。启动前核对API/Broker/Agent/Worker/Portal全部端口空闲，不能只检查Redis。首次28080/23000占用导致登录401，改用38180–38183/33100；勿终止已有SSH转发。

## 2026-10-25 长任务与续租

- 不要用 Run 时长作 claim/visibility TTL；延长任务必须独立续租。renew 只接受未过期的原 token；409立即停止，瞬态失败须有界重试，仅成功响应延长本地截止，持续故障在lease安全边界前停止并保留durable job。claim/renew响应耗时不能加到所有权期限。预算暂停用 pause/checkpoint，不能先 set_status(interrupted) 绕过安全快照检查。
- 手动继续时须注入新窗口提示，解除 checkpoint 中旧收尾提示；有 pending tool 时先记待注入标志，等工具结果配对后再注入，不能在 assistant/tool 中间插用户消息。
- 时间/步数窗口使用独立 budget_window_id：crash、lease自动恢复和ask_user回答延续原窗口，仅用户明确继续预算类暂停才重置（第三轮修正旧job_id设计）。原有 next_step/tool_log invocation 编号不归零，否则交付去重和不可重放工具会撞号。累计 tokens/quota 永不随时间续跑重置。
- 新增原始输入引用时只保存 messages 索引；复制包含 base64 图片的 user_message 到 checkpoint 顶层会绕过 checkpoint_state 的图片引用转换。本轮已有 OCR 体积回归抓到并修正。
- PG 返回带 Asia/Hong_Kong offset 的时间时必须 astimezone(UTC)，不能 replace(tzinfo=UTC)；后者在租约测试中凭空增加8小时。fault dispatcher 断言须停用测试 API 的后台 dispatcher，避免其先发布导致手动 dispatch 返回0；不能把竞态当恢复失败。
- 后台 exec 句柄只对当前沙箱有效，控制文件不进入 workspace 快照。暂停/完成先停止追踪的后台命令再强制快照；恢复不得把旧PID当新任务进程。真实 Linux/provider 与宿主子进程证据须分开记录，见[长任务记录](docs/progress/updates/2026-10-25-long-runs.md)。


## 2026-10-25 长任务来源集成回归

- 同一分支不能由两个worktree并发提交。强制共享checkout会在另一处推进HEAD时让本地旧index看起来是反向暂存；立即隔离到原基线的新分支，不能提交这些反向差异。此次父checkout推进`1d531fe`，修复隔离为`feat/long-runs-sources-fixes`，Portal未改；父任务应cherry-pick修复。
- 完整Python测试须从本worktree的`platform/`启动；从仓库根运行会让SIGKILL子进程找不到`tests.faults`，不是业务恢复失败。集成来源测试的旧“token预算耗尽=failed”断言须更新为ADR0008的可继续interrupted，同时检查暂停结果来源；不能按旧分支各自绿推断合并后绿。
- 来源tab合入后，预览侧栏的区域名为“侧栏”，文件tab为`.pane-tab`，关闭按钮按文件名（如“关闭 sales.xlsx”）；选中文件名用`.pane-tab.on .pane-tab-name`，整栏可用Esc关闭。E2E不能沿用旧`.preview-tab`/“文件预览”/全局“关闭预览”定位，也不能改产品来迁就旧定位器。此次首轮4项E2E失败均由此导致，详见[集成记录](docs/progress/updates/2026-10-25-long-runs-sources.md)。


## 2026-10-25 Workspace GC 的保护集与分页

- GC保护关系应从非终态Run的checkpoint工作区key推导，不能从快照对象列表反推一个IN参数/对象；这会让SQL变量数量随存储增长。仅分页读取ID/runtime_meta并保留非空精确key，再逐项消费有界对象页。
- `_page()`实际每轮仅返回一个`gc_batch_size`页并保存cursor，并非遍历整个存储；仍不要额外list化或为该页构造IN查询。新增回归分别用lazy迭代器抓预读取、用2000对象/SQL参数计数抓快照数量耦合，并以23个暂停Run/页长7检查跨页保护。
- keyset分页不等于数据库一定高效：仅status索引时SQLite可能为ORDER BY id沿主键扫终态历史；0030增加(status,id)。10000行EXPLAIN中SQLite复合索引双条件范围扫描，PG16对每状态100行选择已有status索引+排序，均无顺序全表扫描。计划选择依赖分布，不强制PG使用某一个索引。详见[最终集成复核](docs/progress/updates/2026-10-25-long-runs-sources.md#最终整树复核与额度暂停gc修复)。


## 2026-10-25 长任务 v2

- exec 不能靠模型预判 background；短命令同一个 Broker 调用等待 helper 结束，长命令交回句柄。stdout/stderr 必须保留独立语义。控制 JSON 可能因转义超过原 provider 8K 上限，内部提升至64K，模型输出仍各4K字符/轮询尾部4KB；大中文+控制字符回归不能只用不截断的假 transport。
- 小时自查/收尾提示必须等 pending assistant/tool 配对后再注入；v1 checkpoint 的 review_at/progress_marks 不再是门禁。重复检测必须读 durable tool_log，不依赖会被压缩的 messages；只让真实 steer/人工继续重置窗口，crash 与平台提示不能重置。正常 exec_wait 的 total_bytes/output_tail 变化必须参与比较，idle/duration 不应掩盖重复。
- MockSandbox 也要把 helper 的 /tmp/ailab-background 映射到自身 sandbox 根，避免宿主不同租约共享句柄/上限。真实Linux /proc围栏与mock宿主ps证据仍分开报告。
- 完整测试前添加GNU coreutils/findutils路径；PG测试 conftest 启动会 DROP/CREATE public，同一个临时业务库不能并发启动第二个pytest（包括不使用DB的定向文件）。本轮环境误启及旧spy断言失败不计入最终验收；以最终单独完整运行计数为准。

- yield=0 也可能在首次读取状态前已经完成，不能因零等待就省略完成后的 stdout/stderr。用延迟身份探针让 `printf quick` 在首次状态读取前完成，修复前稳定丢stdout；修复后仍一次Broker往返并返回完整短命令结果。

- Agent的background_jobs可能累积已被helper淘汰的旧句柄，不能按当前进程cap截取该列表做暂停清理；会漏掉真实活跃进程并在快照期间继续写文件。暂停/完成改用租约内部注册表一次kill_all，按进程身份围栏停止所有保留进程。新增“旧句柄占满前N项”回归修复前稳定失败。
- 五服务E2E的显式PG数据库不会像后端tests/conftest自动清schema；重跑前重建自己的一次性E2E库。沿用前一轮库会导致answer.md→answer (2).md等重名及预览测试污染，不能把这种失败当产品回归或放宽文件名断言。


## 2026-10-25 长任务 v2 审查修复

- 持久化重复检测只需规范化参数/结果指纹。不要把 write_file 内容、exec脚本或 read_file 全文复制进每次重写的 tool_log；已消费结果可压缩为有界记账，但保留 result 完成标记、未确认 exec 的 started 标记和独立交付去重。旧 pending 结果尚未配对进 messages，不能提前压缩；没有参数的 v1日志不能虚构调用身份参与检测。
- 长命令仍 running 的 exec_wait 是等待，不是重复操作；必须中断 identical/alternating 后缀，不能过滤后把等待两侧的旧观察拼成假循环。已结束 wait 仍参与检测。
- exec stdout/stderr 需要头部与错误尾部。JSON 协议上限要按序列化后 UTF-8 字节验算，中文、四字节字符和 NUL 转义分别测试；每流1000字符头+8000字符尾（另有省略标记），最坏每字符转义6B，两流约108.6KB；helper序列化后UTF-8须≤120000B，provider内部尾截断上限128000字符。不能等provider截断JSON，也不能为了64KB上限削掉约定的8000字符尾部。
- kill_all 的权威来源是租约内注册表，调用本身也不能以 Agent checkpoint 的 background_jobs 非空为条件。旧 checkpoint 没有该列表也须在暂停/完成快照前清理；控制请求不应计入测试spy的用户exec执行/故障编号。


## 2026-10-25 长任务第三轮独立审查

- **PID 1必须回收退出子进程。** Docker expiry supervisor设SIGCHLD=SIG_IGN；helper每租约一个常驻父进程回收supervisor，Linux启用PR_SET_CHILD_SUBREAPER接住孤儿后代，不依赖OpenSandbox未知PID 1。supervisor自身须恢复SIGCHLD默认行为，否则child.wait可能丢失真实exit code。/proc身份除了starttime还必须拒绝Z；无exit文件但已死的supervisor须失败并可淘汰，不能永久占住8个句柄槽。
- helper/请求内联在Broker命令里会把20,000字符预算吃掉；改为hash校验的每租约安装和一次性JSON请求文件。Mock原来的路径替换只覆盖内联cmd，文件payload也必须映射/workspace、skills和tools，否则宿主回归会全部“完成”却没有文件。真实provider不使用Mock映射。
- running exec_wait不是文件变更。完成wait首次确认才保存；存在运行中后台进程时，快照不能清掉可能继续变动的标记。新checkpoint CAS提交之后才能删除旧工作区key；删除意图与CAS同事务，提交后尝试删除、GC重试，不删除最新durable引用。原0030保护集与有界分页保留。
- 4KiB以下结果也会跨续跑窗口累积超4MiB。已消费日志只保留身份指纹/完成状态；pending结果和started非重放围栏不能提前删。必须独立验算checkpoint JSON UTF-8字节数；超限写有界冻结记录并暂停，不能让413把任务变成FAILED，也不能恢复执行被省略的paid/pending内容。压缩后仍超阈值就暂停，不能每步付费重压。
- budget_window_id与job_id不是同一个概念。只有用户明确继续budget/stall/loop/quota暂停才新窗口；回答ask_user和lease自动恢复只换job。便宜的/run/state必须仍返回真实job_id，否则Agent队列消费会错认所有权。累计token cap不可继续，使用FAILED终态释放会话；日/月额度继续保留quota暂停。步数总结须和out_of_steps_done一起持久化。
- 清理错误不能把已完成回答或预算暂停改成FAILED；kill-all日志警告，最终释放租约。最终验收基线、红绿、资源成本和真实Docker不可用边界见[第三轮记录](docs/progress/updates/2026-10-25-long-runs-review3.md)。
- Playwright Python 的 `page.wait_for_function("async ... => ...")` 不会等待异步谓词：返回的 Promise 本身为真，第一次求值就放行（PR #59 CI 中 ask_user E2E 因此在 Run 尚为 running 时断言）。等 API 状态用 Python 侧带截止时间的轮询（见 `e2e/test_ask_user_ui.py::_wait_status`），回答文字出现早于 Run 终态是正常顺序。

## 2026-10-25 PDF 分享入口 E2E

- UI 操作迁移时同步检查端到端入口：PDF 已移入分享弹窗，打印路径测试须先点分享，再点弹窗中的导出按钮，同时断言旧菜单项不存在。PR #64 提前合并后 E2E 因旧定位器超时，不能将单元测试通过等同发布检查通过；部署须等待修复后的完整 CI。


## 2026-10-25 合并后 CI 与索引计划

- 文档 PR 的范围选择器会跳过产品套件，但 main push 会跑完整套件；PR 总门禁成功不能表述成完整套件已成功，也不能代替合并后固定 SHA 的检查。
- 在共享、稀疏表上查询不存在用户，并关闭 PostgreSQL 顺序扫描后强制断言特定索引，结果会随历史数据/统计变化。计划回归应在独立迁移库内建立固定规模数据、ANALYZE 并使用默认优化器；独立检查专用索引存在，避免索引名子串匹配掩盖缺失。SQLite 文件夹聚合可合理选择已有 `ix_files_folder_listing`，不能因此认定性能退化。详见[本轮记录](docs/progress/updates/2026-10-25-template-query-plan-ci.md)。


## 2026-10-26 思考折叠与Portal增量构建

- 完整思考不等于默认全展开；标题只读短预览，点击时挂载完整原文。展开状态放Turn，避免外层过程关闭/虚拟卸载后丢失；折叠态不挂载Markdown。
- 同一个管理tab重复不只是重复按钮，还要检查面板是否重复挂载/请求。
- 部署Dockerfile的COPY源路径不要带每次变化的版本号；固定源码相对路径、依赖在源码前，部署tag独立。Portal-only可复用后端/Office并仅重建Portal；standalone运行包需带.next/static，未来public目录也须复制。不要把首次构建/缓存命中时间当作CI或完整发布耗时。
- 虚拟Markdown的overscan会在实际可见前挂载；手机正文实测高度大于占位估计。E2E滚底不能看到DOM节点就停止，须验证尾部在滚动容器内且高度稳定，再点击，不能放宽侧栏结果断言。

## 2026-10-26 Crawl4AI 真实包核验

- Crawl4AI 0.7.4 的 `CrawlResult.success` 只表示爬取流程完成，HTTP 403/404/5xx 仍可为 true；返回正文前另查 `status_code`，否则代理拒绝页会冒充文章。配置构造器必须针对安装的真实包验，不能靠 stub 接受 kwargs。
- crawler 的页面请求经代理，但 `http://platform-egress` 服务名仍需集群 DNS；NetworkPolicy 不能把代理地址解析也封死。原生代理契约不等于容器或 k3s 隔离验收。
- 0.7.4 wheel 的 LICENSE 在 Apache-2.0 后追加必需署名要求；保留完整许可证、NOTICE 和 README 署名，不能只写成可选请求。详见[记录](docs/progress/updates/2026-10-26-crawl4ai-reader.md)。


## 2026-10-26 crawler/proxy 的整次调用截止时间

- HTTPX read timeout 仅限制相邻块之间的静默，不能当 total timeout；DNS、浏览器 launch/close 也不能放在 navigation deadline 外。Agent renderer 用一个外层 asyncio.timeout 包住 DNS/请求/读响应，proxy 一个 connect预算覆盖DNS及所有候选地址，crawler同一预算覆盖浏览器上下文生命周期。持续 trickle、慢DNS、永不就绪startup均有原代码红灯证据，见[本轮记录](docs/progress/updates/2026-10-26-merged-reader-validation.md)。
- k3s隔离验收要先证明普通Pod能到达同一公网/私网目标，再测受限Pod拒绝。不能把代理拒绝页当文章，也不能把NetworkPolicy的静态检查/原生浏览器通过当Linux容器隔离证明。Compose没有k3s NetworkPolicy，上线前须独立实现并验收只有代理出口的网络。
- Turbopack不编译项目根外的node_modules符号链接；隔离worktree使用自己的npm ci或根内硬链接副本。全套Python从platform/运行，Ruff配置/第一方import根也依赖正确目录；不要因此重排全库imports。

- Compose容器entrypoint可先以root安装namespace nft后降权；验收docker exec默认仍用Config.User=root，在cap_drop ALL下不再具有DAC绕过权限，会漏读服务UID所有的0700目录。源码完整性检查必须显式使用实际service UID，不能把这种漏读误当镜像缺源码；本轮190文件以UID10001核对通过。


## 2026-10-26 持续browser进程回收

- Python作为容器PID1不会回收Chrome/crashpad孤儿僵尸；短测和healthz200不能证明浏览器仍能启动。使用tini入口；个人Compose自定义网络ENTRYPOINT会覆盖基础镜像入口，必须降权后显式执行tini。不要靠提高pids_limit或周期重启掩盖泄漏。
- async with的__aenter__超时不执行__aexit__，必须finally清理已部分启动的browser/driver并限制清理时间；持续真实render需同时测/proc僵尸、cgroup pids和内存。批量测试用独立限额容器，不能向业务Agent的256MiB cgroup塞额外Python。

- 1GiB crawler双React短测通过仍会在多样复杂页双并发下杀Chrome子进程，且容器PID1不一定OOMKilled。验收必须查cgroup memory.events的oom/oom_kill；默认并发1，忙时明确503，不以提高内存或忽略子进程OOM作通过依据。

- Crawl4AI async_playwright.start在返回handle前被取消会留下活Node driver；finally close加tini仍不足。每次render使用被外部deadline监督的worker，worker设Linux child subreaper；冻结/杀子树并wait之后再释放槽，不能靠库返回句柄才能清理。30ms连续真实启动故障必须检查活进程、重启次数和cgroup memory.events，不能只看最后一次重启后的健康。

## 2026-10-29 文档留存

- 验收可复查不等于保存所有运行输出。仓库只保留结论、版本、复现入口、关键数值与未测边界；原始 JSON/日志/采样放仓库外或 CI artifact，调试结束清理。删输出时先补唯一有用结论并修正引用；保留必要 fixture/脚本/代表截图。详见 AGENTS.md“文档与测试证据精简”。

## Work Office reader 构建边界（2026-10-29）

Work 镜像依赖 vendor 固定 reader，必须从仓库根构建；只哈希 images/sbx-office 会遗漏 parser/锁文件更新。office-read 输出目录必须不存在，修改后换目录；Work 输出不是平台索引。V8 heap ceiling 不是进程 RSS 上限，不可用它推导并发容量。见 [ADR 0025](docs/architecture/adr/0025-work-office-reader.md)。

- 本机 Docker 默认 `/var/run/docker.sock` 不存在不代表 daemon 停机。AILab 使用专用 DOCKER_CONFIG/DOCKER_HOST，先通过 `bash /Users/michael/Documents/AILab-local/bin/docker.sh info` 验证；脚本无执行位时用 bash，不要改默认 context 或另启动 VM。

- office-read必须在Work主提示词可见，不能仅靠附件触发的skill。输出目录用mkdir原子占有，已存在即拒绝；文档内工具指导不应强迫已有工作区路径走file_id附件解析。真实模型验证脚本必须提供可恢复skill错误及交付终止语义，缺接口造成的循环不能误判产品失败。

- 内部文档skill同时供Chat和Work加载，按run mode生成指导；Chat不能出现Work沙箱命令，Work编辑平台文件仍先复制。验证不能用四格式解析通过冒充各格式真实模型问答。

## 2026-10-30 对话固定模式

- 固定模式不能取活动叶节点：历史可能混用模式，fork复制轮次也保留原模式。按首个没有 `result.forked_from_run_id` 的自有Run确定，否则取 `Conversation.forked_from.mode`；编辑/重试保留原轮次模式。请求省略mode需用 `model_fields_set` 区分schema默认work；否则旧Chat会话会被误判切换。队列入队与出队共享准入，幂等重放仍先返回已有身份。详见[记录](docs/progress/updates/2026-10-30-conversation-mode-fixed.md)。

## 2026-10-30 GenUI 状态后端

- `ui_state` 必须独立于 Agent 写入的 `result`，Run/Share 的 response model 也须声明字段，否则 FastAPI 会过滤；fork 的显式 Run 列复制故意不复制 UI 状态。状态更新不受 Run 终态限制。
- Pydantic 校验错误会带原始 input；含 NaN/Infinity 的错误再经 JSONResponse 序列化会抛 500。UI 状态先校验可序列化有限 JSON 与 UTF-8 大小，返回不含原始输入的 422。详见[后端记录](docs/progress/updates/2026-10-30-genui-ui-state-html-backend.md)。

## 2026-10-30 GenUI 真实模型评测

- 仅parser合法会漏掉派生值硬写、表头与行错位、delta相对变化与绝对差混用、正文比较方向错误；P6需独立人工语义核对，不能以100%合法率声称质量通过。记录见[评测报告](docs/progress/updates/2026-10-30-genui-real-model-eval.md)。
- OpenUI store.initialize不会覆盖已有dirty状态；独立控件探针应显式恢复每个声明初值后再改一个变量，否则后续探针受前次状态污染。SVG命名空间HTTP字面量应按要求报告，但不等同实际网络请求。


## 2026-10-30 真模型浏览器验收边界

- 图表必须按 `#turn-${run_id}` 统计；整页统计会把上一轮 SVG 算到本轮，漏掉模型追问改用 HtmlArtifact/CSS 图表的情况。HtmlArtifact 控件在 iframe 内，不能用 Portal 父页 input 数量判断计算器不可交互。
- 内外滚动容器三阶段验收分别用独立浏览器视图观察同一活跃 Run，避免父区上滚让内区离开可视范围、鼠标滚轮落到另一容器。记录 scrollTop/scrollHeight/clientHeight 和真实增长；不要把测试操作互相干扰判为产品失败。当前六例结果见[本轮记录](docs/progress/updates/2026-10-30-genui-chat-render-follow.md#codex-本机部署与真模型验收)。

## 2026-10-30 GenUI catalog v2 评测陷阱

- 合法交互不限Select/Slider/Toggle：Tabs也能满足原地切换，不能被评分器当成缺控件。SVG命名空间URI仅是标识符；字面量告警保留，但要与资源URL/联网分开。
- 组件/表达式接手计算仍会有语义错误：先核对比较的主客体，再判断多/少；HTML测周期需累计模拟时间，不能相减不同帧内的局部时间。parser通过不代表HTML实现正确。
- 本次用户要求保留`/tmp/ailab-genui-eval/`原始回答供审阅，不能沿用旧轮“结束清理”流程。证据只链接[第五轮](docs/progress/updates/2026-10-30-genui-real-model-eval.md#第五轮catalog-v2)，不复制原始输出进仓库。

## 2026-10-31 草稿编辑与测试

- 草稿文本编辑后直接点击「保存新版本」会先触发 textarea blur 保存；blur 期间禁用按钮可能取消点击，未发 confirm。修复要保证一次点击等待已在途写入后继续确认，见 I2 记录的失败截图。通用 `button:hover` 色值也会覆盖记忆批次标题，浅色主题需检查悬停对比度。
- PostgreSQL 不保证无 `ORDER BY` 的事件查询顺序；协议顺序用 `RunEvent.seq`。本机 mock 沙箱测试必须在 PATH 前置 GNU coreutils/findutils，macOS `stat` 不支持 `-c`。性能断言和 Portal 异步加载测试避免与其他整套检查并发。

## 2026-10-31 前缀缓存

- 同一会话段的模型请求必须追加扩展上一请求；刷新旧记忆块、重跑工具、改变工具目录或重新裁剪历史都会使后续前缀失效。权限撤销和压缩须显式记录边界；不能为缓存复用失效权限。具体边界和未测项见 [设计记录](docs/architecture/design/2026-10-31-prefix-cache.md)。

## 2026-11-01 前缀缓存与渲染

- Codex 委派：`--add-dir` 必须用真实路径（`/private/tmp`、`/Users/michael/AILabDisk/local`），符号链接路径会让沙箱拒绝所有命令；`codex exec resume` 会丢掉启动时的 `--add-dir`。会话常在 1–2 个提交后自行结束，且曾因额度用尽静默卡住（看 `~/.codex/sessions/.../rollout-*.jsonl` 的 `rate_limits`）。用户要求：模型用 gpt-6.1-sol medium，测试只跑一次，不要反复全量重跑；明显错误直接修。
- Codex 沙箱内 `bin/docker.sh` 的 `ailab-disk require` 会失败；直接用 `DOCKER_HOST=unix:///Users/michael/AILabDisk/local/colima/ailab-verify/docker.sock DOCKER_CONFIG=/Users/michael/AILabDisk/local/docker-client`。该 daemon 同时运行用户的 `ailab-local-*`，只能动一次性 `c3-*` 资源。
- render 沙箱复用的健康检查不能要求 tmpfs 字节数回到基线：LibreOffice profile 每个文档增长数 KiB，严格比较会让 PPTX 永远不复用。用户文件由清理步骤删除，profile 增长按上限容忍。
- 渲染测速要看分阶段日志（Broker `library render phases=`、Worker `file processing phase=`），不要只看总时长：C3d 的“预热无效”实际原因是解析抢走 standby 槽、预览串行在解析之后、LibreOffice 冷启动。逐页单独 exec 在 runsc 下每页 0.4–0.6 s。
- 直接并发调用 `LibraryRenderer.render` 会得到 `render_busy`：Broker 拒绝、Worker 作业持久重试是设计行为，并发测速要经过 Worker 路径。
- mock 沙箱曾把命令里的 `/tmp` 留在宿主机：渲染预热写 `/tmp/ailab-warm`、健康检查清 `/tmp`。宿主机残留的 `warmup.pdf` 让 macOS 上的预热“假成功”，E2E 因此一直通过，CI（无 LibreOffice）则会全部预览失败。现在 mock 的 `/tmp` 映射到沙箱目录并设 `TMPDIR`，`render_sandbox_health.py` 从自身位置和 `TMPDIR` 取路径；mock 声明 `office_listener = False`，渲染不预热、不加 `AILAB_LO_STRICT`、健康检查只做清理。真实镜像仍要求 listener。
- 生成页（HtmlArtifact）的高度不能用 `documentElement.scrollHeight`：它不会小于 iframe 自身高度，页面变短后框永远不缩；`height:100%` 的页面则永远不长。按 body 内容范围（Range 包围盒 + 内边距/外边距）测量，见 `lib/frame.ts` 的 `RUN_HELPER`。
- 固定工具目录约 2.8k token，并且计入压缩预算。上下文窗口很小（如 3.3k）的测试夹具放不下任何一轮历史，会报 `history exceeds model budget before compaction`，这是夹具问题，不是产品回归。
- 前缀缓存不只看 messages/tools 是否仅追加：请求级参数也进提示模板。DeepSeek 实测同一 9.5k 前缀，`thinking: disabled` 或 `tool_choice: none`（后者把工具从模板删掉）都让命中从 9.3k 掉到 1.2k。续写会话的调用（压缩摘要、步数用尽收尾）必须与主循环同形，见 `AgentLoop._answer_without_tools`。用直接调用 API 的小探针隔离参数影响，几分钱即可，不要靠整轮验收猜。
- 验收用的强制压缩小窗口（如 24k、阈值 0.6）会让 PPT 这类带图片的 Work 任务在两次查看后 BudgetPause 并兜底交付，不能当作 PPT 流程结论；命中率也被频繁压缩拉低，要单列压缩相关调用。交付文件数 = 1 不等于模型主动 `deliver_file` 或 Run succeeded，轮询要识别 interrupted。
