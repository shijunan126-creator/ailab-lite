# 2026-10-24 PPT 模板集成测试与干净部署验收

## 授权与代码基线

主工作树 `feat/ppt-templates-portal`，起点与部署源码固定为 `b2c8394047279c634802a8ef9591d8bf275fac56`，本地 merge-base `main@6b6825d`；未 fetch，不声称远端最新。用户授权全套验证、后端根因修复、小提交、保全配置后清空旧测试部署并从空库部署、真实模型和模板验收、记录资源与清理测试数据。禁止修改 `platform/portal/`、push、PR、merge；三份未跟踪 `.DS_Store` 保持原状。本次新授权覆盖旧记录中的未授权部署/必须备份要求，仅针对本次测试部署。

个人配置、账号、地址、部署脚本、原始日志和硬件诊断均仅留仓库外本机交接；本文只保留产品基线、测试结论和可公开的验收边界。

## Part 1 当前验证

- Ruff check 与 format --check 通过，329 文件；`git diff --check` 通过。
- 固定 `b2c8394` 的完整 SQLite：**1123 passed / 118 skipped / 0 failed**。完整 PostgreSQL16（UTF8/C locale）+ Redis：**1208 passed / 33 skipped / 0 failed**。使用独立 initdb、非默认端口和空 Redis，未用开发库；PG、Redis均已停止，相关监听关闭。
- 本轮最终完整五服务 E2E：**88 passed / 0 failed / 0 skipped，260.24秒**。真实 HTTP/outbox/Redis/Worker/Portal production build，模型和 sandbox 为 mock；不作为真实 Office 或付费模型部署验收。
- Portal `npm test` **33 passed**，隔离副本 `npm run build` 通过。运行库现有 E2E fixture，不修改 Portal 源码、tsconfig 或开发 `.next`。
- `kubectl kustomize` 成功渲染 `platform/deploy/overlays/{dev,lite}` 与根目录 `deploy/overlays/lite`。这是静态渲染；未对新 K3S 集群 apply 或重新验收控制器/CNI/gVisor 联合运行。

SQLite/PG日志因项目默认 `-q` 与命令 `-q` 叠加不显示总结，已按全部进度行核对1123/118与1208/33，均抵达100%，退出成功；没有用旧记录计数替代。SQLite跳过未配置PG/Redis及外部组件；PG+Redis33项仍需外部S3、真实OSB/Docker/网络、ClamAV、OIDC等显式契约环境，不把跳过记为通过。

### E2E 适配与历史失败

原生模型 `select` 已变为按钮/listbox；原生确认框已变为 `alertdialog`；个人技能位于“更多技能”菜单；旧品牌 PPT 槽已由模板注册表取代，品牌槽保留 Word 模板。只修改 `platform/e2e/`，保留模型可见列表/权重/默认项/选择持久化、确认删除成功和失败、品牌分析与设置持久化、技能使用和隐藏断言。模型选择按可见选项点击，不通过localStorage或API绕过UI；确认后再验证原有业务结果。

首轮因系统性旧选择器中断，不能计为全套通过；第一次适配后 **86通过/2失败**，分别为测试误加无关的记忆重载确认、仍使用旧个人技能快捷入口。纠正后完整重跑88项通过，不放宽产品断言。未发现须修改Portal产品代码的已确认缺陷。

## Part 2/3 进行中

已在仓库外保全并校验配置，固定Git归档，清除旧测试部署数据/镜像/缓存并冷构建platform、Portal、sbx-office。外部存储中断导致启动失败及依赖镜像层损坏，正在重建专用容器存储和镜像；尚不声称部署完成、空库0028验收、真实模板/无模板模型运行或最终资源数字通过。

下一步：完成固定镜像启动并核对空库0028、健康与新管理员登录；公司/个人模板首屏JPEG、真实模型有模板与无模板各一份、资料库PPTX/DOCX浏览器预览、部署queue/steer/share/inbox，收尾清理并记录资源。历史发布已覆盖的非新增功能沿用明确的历史边界，不重复宣称本次实测。
