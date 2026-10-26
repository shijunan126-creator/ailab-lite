# RFI正文与标签首字修复

2026-10-26，用户要求修复截图RFI正文为空和网页tab从标题中间显示。工作树 `/private/tmp/ailab-web-source-tabs`，分支 `fix/rfi-reader-title-start`，base `origin/main@438bd63`，产品 `f2f1059`。原主工作目录修改不动；本轮不修改部署/业务数据，不新增网络依赖或站点特殊适配。

## 根因及修复

- 精确截图页：`https://www.rfi.fr/cn/欧洲/20261022-法国社会动员-马克龙收紧对示威活动的管制`。用现有public-only客户端真实抓取，返回423681B HTML、标题正确、fetch未截断，却提取0块。正文就在服务端HTML中；`body class="u-header-sticky u-header-with-tag-list u-playlist-enabled"`触发NOISE_HINT，导致整页被过滤。html/body全站类名不应用于导航子树判定；hidden/aria-hidden和真实header/nav/footer等子树规则保留。结构化最小回归使用合成内容，修复前失败、修复后通过，无需把整份站点正文写入仓库。
- 全局button的inline-flex/justify-content:center作用于长标签的匿名文本项；溢出时开头被裁掉，即使写了ellipsis也不能保证从首字开始。名称改block、text-align:left和可收缩flex项，尾部省略；选中时清名称内部scrollLeft，标签栏按整个tab起点定位，避免窄条只露中间。

## 验证

- 本地网页Python专项26通过，含RFI式文档根class的红绿回归及原有提取/安全边界。
- 真实截图RFI页修复后23块，1068正文字符（连块换行计1090），原文章12段主要段落全部保留；另外简体/繁体两条20261024报道均29块、1355字符、无截断。真实外网结果可能随站点变化，不以mock冒充真实抓取。
- 五服务浏览器6通过/7无关场景未选，36.05秒。包含桌面/手机独立webtabs和原预览Back回归、新增5个长标题标签0→4→1切换，使用DOM Range测量首个字形必须落在名称和tab条可视交集内，并检查left对齐/scrollLeft0。使用生产Portal构建和隔离SQLite/Redis，外网页面使用确定性HTML夹具；真实RFI抓取另验。Portal42/build/TypeScript通过，Ruff check及382文件format通过。
- 产品提交f2f1059的SQLite+独立Redis全套1362通过/95跳过/0失败，196.69秒；本轮未本地运行PostgreSQL、整套E2E/付费模型/真实手机/目标容量。无SQL/迁移/后台任务/新依赖；样式变化无额外轮询，提取仅调整根节点判断，资源限额不变。
- 精确截图页提取已本地验证，生产仍是438bd63；本轮尚未push/PR/merge/deploy。临时五服务由夹具停止，专用Redis16471核对pidfile后shutdown，13470/18470–18473/16471均无监听。下一步本地交付完成，等待后续发布授权，不把旧部署记录当作本次上线证明。


## 同步main冲突处理

PR84合入main53eff6e后，PR85仅docs/progress/current.md冲突。保留两轮交接，校正PR84已合状态，不回滚主线产品代码；RFI与标题修复产品代码无冲突。当前发布候选需更新为同步后的固定提交，包含主线既有沙箱额度/闲置回收；本机池仍1槽，保持原配置，不冒充新增真实回收验收。网页相关专项与最终PR/main完整CI作为合并验证。

同步后网页/沙箱回收/runtime专项74通过，Ruff383文件通过。首轮未设置仓库要求的GNU PATH，mock快照执行macOS stat -c失败导致1失败/73通过；按HANDOFF补coreutils/findutils路径后74/74通过，未修改产品或降低断言。合并后主线代码对origin/main无差异，网页产品修复保留；候选按新固定提交重建。
