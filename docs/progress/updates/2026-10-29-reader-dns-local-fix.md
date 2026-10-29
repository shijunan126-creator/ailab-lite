# 本机网页预览全域fake-IP DNS修复

用户反馈来源网页返回`refusing non-public address`，不只头条。已确认本机VPN DNS返回198.18.*虚拟地址，经Colima默认DNS进入API/Agent等容器；头条、python.org、example.com均受影响。旧7ad25a1与当前公网检查代码一致，文件上下文改造没有新增这一拦截。

本机部署配置修复：给API、Agent、reader-egress、SearXNG指定真实公共DNS，避免继承Mac VPN的fake-IP；隔离出口nft只额外放行这两个DNS服务器的UDP/TCP53，继续拒绝私网和虚拟地址。未修改Mac全局DNS/VPN、未放宽产品SSRF规则、未改DB/账号/文件，不发模型调用。重建相关容器，旧配置/镜像保留；Compose和构建/同步入口已持久化。

实际验证：登录后的reader/render头条、python.org/about、example.com均HTTP200/HTML，Playwright网页不再显示refusing错误；四个服务三域名解析均为公网。127.0.0.1、169.254.169.254及198.18.0.137仍被拒。crawler通过隔离代理获取example.com成功，返回独立title=Example Domain及可读正文。外部quotes JS探针超时，未将所有外站可用性当成本次DNS修复结论。数据库、OCR和Office服务未重建，现有业务数据不变。

个人DNS/网络配置、回滚副本、原始日志与网页证据按本机部署边界保存在运行HANDOFF，不入Git。下一步用户刷新页面并重新打开来源链接；其他外站反爬、登录或超时应按实际错误另外处理。无push/PR/merge。
