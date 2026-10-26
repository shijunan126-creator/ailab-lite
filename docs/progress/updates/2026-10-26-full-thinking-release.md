# 完整思考发布

用户授权PR、合并及数据保留部署。发布分支`fix/full-thinking-release`，独立工作树 `/private/tmp/ailab-full-thinking-release`，起点`ab0e8d9`。`7859825`合并最新main`f878ce1`，保留网页/HTML面板并将网页链接回调传入虚拟Markdown的每一块；`41c71a2`补桌面/手机回归。PR [#78](https://github.com/shijunan126-creator/ailab-lite/pull/78)，首轮head`8747dda`。

合并树本地：Portal41项/build/TypeScript、Ruff361，SQLite1270通过/132跳过；106浏览器104通过，记忆Chat/Work两场景登录审计被SQLite写锁阻塞约32秒。该后端代码相对main未变，停止并行构建/测试后单独复验记忆2＋新网页2共4通过；保留写锁风险，不冒充已修复。

首轮远端CI：SQLite、PG、存储契约、quality、shell等成功；E2E104通过/1跳过（Office工具缺失）/1失败，CI总门禁因该E2E失败。失败截图证明表格已正常显示右侧实时预览，旧测试仅等待`.viewer`故超时。本次改为在唯一实际预览面板中验证表格内容、关闭重开及再修改，不改产品、不增加skip或超时。候选镜像及运行源码不受本测试改动影响。

修正后定向：表格关闭重开/再修改与桌面/手机实时侧栏3通过、0跳过，85.37秒；夹具内Portal41项/build通过。Ruff与diff检查通过。

当前未合并/未部署；原服务留运行。候选已构建并核对179平台/102Portal源码；部署前复核、完整备份/实际解包与保留数据切换脚本已准备在本机独立运维目录，不入Git。下一步：推送本次测试修正，等待PR全部检查成功，再固定head合并与部署并追加结果。保持原业务数据、账号、配置和旧镜像；不调用付费模型。
