# main 同步与 CI 回归修复

基线 `origin/main@1d07d7d4e6938730f2082a71df587b543ac590c9`，独立分支 `fix/main-ci-regressions`。本地 main 从 `e71a60f` 快进至该 SHA；主目录仍在 `fix/full-thinking-rendering@ab0e8d9`，保留全部既有设计/清理/个人文件改动。

## 失败证据与根因

- [#102 main CI](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40837774141)：Python 双库、quality 等通过，E2E 为 124 passed / 1 skipped / 1 failed。记忆治理桌面测试在暂停保存后刷新，未见“恢复”；精确“保存”按钮在请求开始时已改名“保存中…”，旧等待立即放行，刷新可中断未完成请求。
- [#103 main CI](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40840837781)：Python 双库、quality 等通过，E2E 为 124 passed / 1 skipped / 1 setup error。trace 显示匿名 GET `/` 返回 500；Portal 日志为代理 `http://localhost:13000/login` 时 socket hang up。当前 Next 16.4.2 的 NextRequest 默认把 127.0.0.1 规范化为 localhost，router 对原始监听 origin 比较后把重写判作外部 HTTP 代理。
- [#104 main CI](https://github.com/shijunan126-creator/ailab-lite/actions/runs/40842199807) 最终通过。后两次提交为文档变更，main push 仍全量检查；上述失败点不同且间歇出现，不能归结为文档修改造成同一产品回归。

## 修复与成本

- 记忆 E2E 的四处保存等待同时匹配“保存”和“保存中…”，等待操作真正完成后再刷新或切换；不改产品保存逻辑、不加重试或放宽失败断言。
- Portal 使用当前 Next 内置 `skipProxyUrlNormalize` 配置保留原始 URL，登录页仍同 URL 内部重写。新增使用真实 NextRequest/NextResponse 及 router origin 比较函数的测试，覆盖 IPv4/IPv6 loopback、公共域名、已登录请求。
- 本地完整 Python 首次遇到既有 macOS 回收探针 EPERM，单项复核通过。测试对 Darwin 的临时 EPERM 继续按原截止等待，仍必须收到进程组不存在的结果；Linux 行为及产品 helper 不变。
- 不新增依赖、后台任务或业务查询；匿名登录去掉一次不必要的 HTTP 自代理。未作吞吐、CPU/RSS或部署性能实测。

## 本轮验证

修复前新增 origin 回归稳定失败：实际 `http://localhost:13000/login`，预期内部 `/login`。修复后 Portal 单测 44/44、Office reader 15/15、CI 门禁 28/28、Ruff check/format（427 文件）、生产构建与五服务浏览器 E2E 126/126 通过；浏览器运行约 362 秒，Portal 日志未见原 socket hang up。完整 Python 首轮为 1480 passed / 104 skipped / 1 failed（上述 macOS 探针），单项复核 1 passed；修正后完整重验 1481 passed / 104 skipped（214.49 秒）；跳过为未配置的外部组件/PG/OTel 等环境用例，由本 PR 远端 CI 补双库与真实契约。原始输出只放仓库外临时目录。

用户后续明确授权直接修复、push、PR、merge，无需再确认。下一步完成最终 Python 检查并提交 PR，等该 head 的 CI 通过后合并并同步本地 main；不部署。修复后远端固定 SHA 的 CI 尚未运行；双库远端旧检查不等于本修复的双库验收。
