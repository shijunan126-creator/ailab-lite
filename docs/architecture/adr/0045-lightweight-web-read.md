# ADR 0045 · 轻量网页读取：删除浏览器渲染
状态：已采纳（2026-11-16），取代 [0021](0021-isolated-browser-rendering-for-read-url.md)。

## Context

`read_url` 先用 httpx 直接抓取 + Trafilatura 抽正文，弱结果再交给隔离的 Crawl4AI 浏览器。对历史 180 个 URL 的实测：直接抓取成功 88%，失败的 21 个里浏览器只救回 1 个，平均多花 14 秒；抓取镜像 2.88 GB、常驻 370 MB，还要维护独立出口代理和网络策略。与此同时，浏览器拿不到的 403 里有相当一部分只是 User-Agent 与 TLS 指纹不匹配；`Accept: text/markdown` 有约 10% 站点直接返回更小的 Markdown；Wayback 快照能在 2–6 秒内救回被拦截的新闻页。继续为 1/180 的收益保留一条会执行页面脚本的链路不划算。

## Decision

1. **删除浏览器渲染和它的出口代理。** 删除 `images/crawler`、`platform/ailab_platform/egress.py`、`platform/deploy/base/crawler`、相关 Secret、镜像构建、CI job、配置和测试。`/v1/reader/render` 只做一次公网抓取，失败时返回“请打开原站”的提示页。
2. **直接抓取用 curl_cffi 模拟 Chrome。** 请求头、TLS 和 HTTP/2 指纹来自 `impersonate="chrome"`；每个主机名仍先用 `web.public_addresses` 解析并检查全部地址是公网，再通过 libcurl 的 `CURLOPT_RESOLVE` 把 `host:port` 钉到检查过的 IP。RESOLVE 在 libcurl 内生效，不会发生第二次 DNS 解析；重定向每次重新校验并重新解析。`NOPROXY "*"`、`trust_env=False`、`discard_cookies=True` 保证不走环境代理、不带 cookie。下载路径继续用 httpx。
3. **Markdown 协商，失败回退 HTML。** `read_url` 发送 `Accept: text/markdown, text/html;q=0.9, */*;q=0.8`；响应是 `text/markdown` 时直接作为正文，不跑 Trafilatura。带这个 Accept 得到 403 时用普通 HTML Accept 重试一次（例如 openai.com）。
4. **脚本空壳先从页面自带的 JSON 补救。** 不引入 JS 引擎。短正文或 `#root`/`#app`/`#__next` 空壳页依次尝试 JSON-LD 的 `articleBody`、`__NEXT_DATA__` 里的长文本字段、`og:description`/`meta description`；补救不到才返回原来的弱结果或错误。
5. **被拦截时读 Wayback。** 直接抓取 401/403/429，或标题/内容命中验证页特征时，查 `archive.org/wayback/available`；有快照就用同一套公网抓取读 `https://web.archive.org/web/{timestamp}id_/{url}`（`id_` 返回原始页面），结果带 `archived_at` 和“可能不是最新”的说明。快照不存在或剩余时间不足 3 秒时返回原错误。
6. **一次读取一个总时限。** `AILAB_WEB_READ_DEADLINE_SECONDS`（默认 12 秒）覆盖 DNS、连接、跳转、读取、Markdown 重试和 Wayback；DNS 单独最多 3 秒。超时、被拦截且无存档都明确提示换来源。下载仍用 `web_transfer_deadline_seconds`。
7. **结果形状向后兼容并支持续读。** `read_url(url, max_chars=None, start=0)` 返回 `text` 的 `[start, start+limit)` 段；还有内容时返回 `next_start`，`truncated` 表示还有没返回的内容。链接优先取正文里的链接，整页 `<a>` 只作兜底，上限 12 条，保留 `net_tools.sources_from` 的来源白名单。
8. **解析有界。** HTML 抽取在线程里跑，进程内最多同时 2 个；单页不超过 `web_max_bytes`，不一次保留多份完整页面。

## Consequences

- Agent 不再执行页面脚本，威胁模型回到 ADR 0007 的“只读公网字节”；不再需要为浏览器维护 1 GiB 内存、容器 PID 回收、出口代理和独立 NetworkPolicy。
- 直接抓取的 Chrome 指纹能多救回一部分站点，但 Cloudflare 等仍可能拒绝；产品不承诺绕过人机验证，只返回可读错误或存档快照。
- 结构化数据只覆盖页面自己愿意暴露的正文；纯客户端渲染且没有 JSON-LD/Next 数据的页面仍会失败。
- Wayback 依赖 archive.org 的可用性和延迟；查不到或时间不够时不会拖住正常错误。
- `curl_cffi` 新增一个带二进制 wheel 的运行时依赖；锁文件固定到经过验证的版本，secure 档位不需要新增配置。
