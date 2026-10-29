# 文件上下文/Office Runtime本机体验部署

用户明确授权部署当前改造，覆盖本轮此前“不部署”限制；仍不push/PR/merge。部署已完成，供用户自行网页体验。

## 固定版本与部署范围

工作树`feat/file-context-office-runtime`，base/HEAD`e71a60f8b4e0ea139b12be80d14cb38a627a2db7`加未提交实现；主目录旧分支、视觉设计、旧Memory资料保留。部署源码是明确的dirty快照`file-context-35168e7e6c95`，815个源/构建文件，摘要`35168e7e6c95242d2cf934d1bd65a43acc0e11e8f7484af6d28da883b73d2abf`；不冒充已合并main。新增显式CPU OCR镜像构建定义`images/rapidocr/Dockerfile.cpu`，产品通用executor默认CANN不变。

沿用原本机VM与入口、账号和持久卷；固定应用/Portal镜像，Office Runtime在Broker系统沙箱运行，独立OCR服务只在内部网络可达，模型预置只读、不向用户沙箱安装、不对外发布端口。原有用户Office、搜索和网页读取组件保持原镜像。个人模型目录与凭据只在本机运维目录，不进入仓库或镜像源快照。

## 备份和迁移

切换前无活动Run，停止五个业务写入/入口服务；完整业务卷备份实际解包验证SQLite0033、quick_check正常/FK0和11原文件SHA，并保存内部磁盘独立副本。原镜像/Compose/脚本及备份保留。迁移0033→0034→0035→0036成功，4原账号摘要、原六表ID和原11文件均保留，个人凭据/模型目录字节不变。最终16ready文件（5个验收新增样本）校验正常，SQLite0036/quick_check正常/FK0。

回滚需要停新写入者并保存新卷，在新空volume恢复迁移前完整备份、恢复旧Compose/镜像；旧API不可直接连接0036新库。交用户使用后如产生新写入，不能自动用旧备份覆盖；须先保存新数据另行处理。实际备份路径/hash和回滚脚本只留个人运行HANDOFF。

## 必要验收

- 真实网页登录与httpOnly session、已有会话/资料库读取成功。
- PDF/DOCX/XLSX/PPTX四份上游样本经实际ClamAV→Worker→Broker系统runsc容器→API块索引完整闭环，不是mock parser。
- 一张合成中文图片经实际独立RapidOCR CPU识别、缓存与导出，合同编号AL-219、金额12345.67、日期2031-06-18准确；网页文件菜单“提取文字→下载文字”可操作。
- DeepSeek一次实际文档Chat成功读取Word标题Quarterly Report和North收入120；现有资料库摘要配置触发4次summary调用，均入账，OCR不调用LLM。
- 1440桌面/390手机宽度Playwright实际浏览器检查资料库和文档对话，无pageerror/横向溢出。截图含个人界面，按本机数据边界只留运行证据，不复制到仓库。
- 运行镜像身份、四后端源码hash匹配固定快照；12容器running，业务及OCR健康、restart0、无OOM。单次快照五业务约61–141MiB、OCR325MiB/1GiB；reader镜像逻辑约115MiB、OCR镜像约302MiB。均不代表峰值/容量。

按用户要求不重复全套开发测试或长时间负载。真实NPU/CANN、代表性识别质量、500轮10代、数百人/多节点容量与外置USB稳定性仍是未验边界。保留“文件上下文体验验收”项目与5份样本和已成功的文档问答供用户继续体验。

日常启动/同步/构建入口与current-deploy已更新为该固定版本。详细配置、备份、源清单、构建/切换日志、数据库/账号完整性、资源与浏览器结果按规则留本机运维目录。没有远端发布或仓库合并。
