# 原生查看扫描恢复与错误分流

## 根因与改动

基线 `main@910f1525`，分支 `fix/native-view-scan-recovery`；用户授权修复后提交并提 PR，未授权本轮合并或部署。#192 只恢复显式分页预览；旧文件原生打开时，缺扫描记录返回 409，Portal 的统一 catch 将它误当兼容性失败，启动分页渲染。

- API：原生入口对旧 sandbox 交付件复用 file outbox 扫描任务；持用户锁重新读取并发去重。扫描失败/任务过期返回明确错误；文件库与分享复用同一入口。
- Worker：复用真实扫描、流式摘要和大小核对；原生恢复不预热渲染沙箱，不解析或排分页。成功清除恢复标记，普通打开直接返回原生准入元数据。
- Portal：原生元数据接口的安全/权限/网络失败显示错误，不挂载分页组件；pending 轮询最多 5 分钟，关闭取消。格式/大小准入、索引不兼容及 renderer 失败仍可分页回退。

## 当前验证

- `ruff check`、`ruff format --check`：5 个相关 Python 文件通过；`git diff --check` 通过。
- `PYTHONPATH=. pytest tests/test_file_previews.py tests/test_document_view.py tests/test_eager_previews.py tests/faults/test_document_view.py -q -p no:cacheprovider --tb=short`：111 passed。
- `npm test -- tests/office-view.test.tsx tests/office-view-boot.test.tsx`：62 passed。
- `npm run build -- --webpack`：编译、TypeScript 和生产页面生成通过。默认 Turbopack 构建因独立目录复用的 node_modules 符号链接超出解析根目录失败；未为此更改产品配置。
- 首轮 Python 回归 1 项失败：新增去重断言把 fixture 初始上传任务计入扫描恢复次数；修为以请求前任务集合为基线，验证只新增一个恢复任务，完整上述范围复跑通过。

## 成本与边界

无依赖、迁移或新配置；正常 clean 文件不新增扫描/渲染。旧文件首次打开增加一次现有受限扫描和流式 hash，后续复用结果；等待期沿用 1.5 秒轮询，最多约 201 次元数据请求。未测真实扫描 CPU/RSS/耗时，未做浏览器、PostgreSQL、完整 CI 或部署验收，不改变既有外部验收结论。

并发 UI 工作区保持原样；本修复单独提交并提 PR，下一步以最终 head CI 为准，浏览器实测仍待完成。

## 既有渲染耗时调查

用户随后要求先提 PR，并询问误入分页的慢来自预热还是渲染；只读取既有 Broker/Worker 日志，没有重渲染或调整资源。

- 2026-11-13 11:30（香港时间）原失败作业：Broker 总计 35.572 秒，`lease_source=cold`，获取/就绪 27.427 秒，注入 0.382 秒，首次转换 4.194 秒、第一页栅格化 0.245 秒。主要耗时在冷沙箱获取/就绪；发布阶段随后因旧扫描记录缺失失败。日志没有把获取阶段细分为容器、服务和 LibreOffice listener 各自耗时。
- 当日 12:55 重试：Worker 总计 19.194 秒，调度槽等待 0.006 秒；Broker 总计 18.831 秒，同样 `cold`。获取/就绪 8.193 秒，注入 0.259 秒，首屏阶段 2.677 秒（已包含转换 1.389 秒和栅格化 0.199 秒，不能重复相加），PDF 缓存导出 0.266 秒，余下 8 页栅格化/归档传输/逐页发布合计 7.390 秒。
- 重试另有扫描 0.937 秒；从原生入口 409 到第一页发布约 12.4 秒、全部完成约 20.2 秒。既有日志不能再分解余页 CPU 与传输占比，也不代表浏览器实际绘制耗时。
- 结论：首次失败主要慢在冷启动；成功重试则是冷启动加多页生成/回传两部分，不能把所有等待归为 LibreOffice 转换慢。正常浏览器原生打开应避免整条分页链路，本 PR 修复该分流。
