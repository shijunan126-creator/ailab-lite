# 2026-10-29 来源网页403：读取和预览链路对齐

## 真实问题、基线和范围

用户报告力扣旧讨论链接及两个知乎专栏链接在网页预览中显示 `{"detail":"HTTP 403 ..."}`。工作树 `.worktrees/file-context-ocr`，分支 `feat/file-context-office-runtime`，HEAD/base `e71a60f8b4e0ea139b12be80d14cb38a627a2db7` 加既有dirty实现；保留现有文件方案、Portal标签修复和本机DNS修复。无push/PR/merge。沿用用户本机部署授权，本次仅更新API/crawler；不迁移数据库、不改模型配置/业务数据、不调用模型。

## 根因证据

不是之前198.18.*的DNS拒绝。静态公共HTTP请求对这三个地址均实际返回403。Agent的 `read_url` 已有静态→隔离浏览器恢复路径，但Portal预览的 `routes_reader.reader_render` 只调用 `fetch_html`；它把异常转成HTTPException，iframe直接显示JSON。

同环境实际隔离浏览器：力扣正文读取成功（约7.56秒、5969字符），两个知乎页仍失败。不能把“浏览器启动成功”或HTTP200反爬页当文章可读，更不能把原站拒绝归咎于平台SSRF检查。

## 实现

- 预览保留静态快路径，仅对现有RENDER_STATUSES和可恢复传输失败做一次隔离浏览器快照重试；DNS/私网/证书拒绝不重试。不对所有页面默认启动Chromium。
- 复用现有crawler `/read`，新增可选 `include_html`，默认Markdown工具合同不变。输出DOM最多2MiB，不静默截断；原有单页槽位、deadline、子进程回收和public-only egress不变，监督器输出读取上限4MiB以容纳JSON转义。API总deadline45秒。
- 浏览器DOM仍经现有base/refresh整理，返回原有脚本/子frame/表单禁用CSP。访问错误保留失败HTTP状态，iframe改为中文HTML，提示用右上角原站按钮，不伪造正文。
- API增加crawler专用credential及精确NetworkPolicy ingress（API/Agent两者），secure模式检查API的crawler token。浏览器容器仍无业务DB/model/service身份，出口仍仅egress代理。Kubernetes模板同步；本机namespace规则只增加API固定来源IP到crawler8090，不放宽egress或私网限制。
- 决策更新见[ADR0021](../../architecture/adr/0021-isolated-browser-rendering-for-read-url.md)。

## 验证与部署

`test_reader.py` + `test_web_crawler.py` 首轮56通过；补齐API token和精确NetworkPolicy断言后，相关web/deployment组45通过（与前轮重叠，不累计冒充独立验收数量）。Ruff/check/format与diff检查通过。crawler最终派生镜像内实际安装依赖和Linux进程回收13项通过。未重复Python/PG/Portal全套；本次无Portal源变更，未重建Portal。

本机以固定六文件dirty增量快照 `reader-403-dd094497c120` 派生API/crawler镜像，API基于现有文件方案镜像、crawler基于现有受限镜像；镜像内源哈希逐文件核对。Portal仍为附件标签版本，其余服务、DNS、egress镜像和数据保持。Compose/build/sync均持久化候选路径及回滚前配置，个人材料只留本机部署目录。

真实登录后的Portal同源reader接口、Chromium正文/错误页截图：力扣显示正文；知乎两篇仍拒绝，显示明确中文错误而非JSON。普通公共页和loopback/metadata/fake-IP直连一并回归；详细最终状态和资源样本见[脱敏证据](../../reviews/file-context-2026-10-29/reader-preview-403.json)。control第一次断言错把Example Domain标题当正文，实际HTTP200正文是documentation examples说明；修正内容断言后只重跑control/安全部分，未以该断言失败改产品网络代码。

## 性能与残余边界

本机一次浏览器恢复约7秒，明显比约0.1–0.2秒的静态拒绝更贵；只在需要恢复时发生。最终力扣预览约6.92秒、两个知乎拒绝约7.67/7.35秒；API/RSS样本150.7MiB、crawler回收后37.76MiB（不是浏览器峰值）。API/crawler镜像逻辑大小相对已有基底仅增加22993/9076字节（不是独立下载全部镜像成本）；进程瞬时资源样本与响应耗时见证据，镜像是已有依赖的代码增量，没有新浏览器/解析依赖。现有1GiB/single-page槽位控制瞬时成本，繁忙会明确失败，不代表数百人并行浏览器容量已经验收。

两个知乎地址仍受原站访问控制影响，不承诺能读；用户可在自己浏览器直接访问原站。本次没有接入个人cookies/网站登录态、验证码处理或全面HTTP200脚本壳检测，也未做全网覆盖承诺。
