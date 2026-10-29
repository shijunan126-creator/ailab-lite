# 办公内容、OCR与等待修复

用户授权修复并验证PDF问题，同时检查其他办公文件与含图/纯图场景；未授权部署、push、PR或merge。本轮独立工作树`.worktrees/document-reading-reliability`、分支`fix/document-reading-reliability`，基于本地`origin/main@202e48a79db88e125d7f7ba97720cdddb4483a71`（包含#106），未fetch。主目录、文件方案工作树及用户已有修改保留。

## 结果与行为

- 修复扫描页占位说明被当正文、Chat/Work跳过已有OCR缓存、PDF重复提取重建generation/渲染、旧无扫描凭据任务ACK后永久pending，以及附件身份工具成功被记录成失败。
- 四种格式提供原生文字与视觉覆盖事实；含图Excel的合法OOXML绘图曾使ExcelJS读取失败，文字通道现在忽略绘图部件并保留原文件给视觉渲染。纯图、正文夹图、Word包内媒体、PPT形状和PDF绘图不再静默等同于纯文字。
- OCR完成直接返回正文；Agent自动持久等待，不再让pending进入模型循环或启动沙箱sleep。完成事务唤醒、每秒有界补偿、超时、取消、来源失效和恢复准入共同约束后续执行。Portal自动等待保持SSE、停止按钮与发送互斥，不出现手动继续。
- Chat视觉模型先准备完整有界页面包并计预算，首请求附正文与页面；视觉页面丢失时不发送不完整内容。非视觉模型只覆盖文字并显式说明图像语义缺失；没有可读文字的纯图文档提示选择视觉模型。Work按需查看四格式页面，返回覆盖与截断信息。精确边界、资源取舍及旧checkpoint/cache兼容见[ADR 0026](../../architecture/adr/0026-document-visual-coverage-and-file-waits.md)。

## 验证

最终40份产品/测试文件基线摘要`6ed4e5efa26d4437753bab80f71db22c54b658562c362e4ada2ccb9e9a203a96`，基于`202e48a`；组测后重新校验源摘要未变。原始输出在仓库外临时目录，调试收尾删除，不提交。

| 检查 | 结果 |
| --- | --- |
| SQLite＋Redis，`cd platform; .venv/bin/pytest tests` | 1502 passed / 103 skipped / 0 failed |
| PostgreSQL 16＋Redis，同一完整套件 | 1550 passed / 55 skipped / 0 failed |
| 五服务真实浏览器全套（隔离API/Worker/Broker/Agent/Portal） | 128 passed / 0 skipped / 0 failed |
| Portal `npm test` / `npm run build` | 45 passed / build通过 |
| 固定Office核心 `npm test` / build | 15 passed / build通过 |
| 真实离线容器：含图四格式原生读取＋页面渲染；系统reader两并发四格式合同 | 全部通过 / 1 contract passed |
| Ruff check / format / 服务导入 | 425文件通过 / IMPORTS_OK |

最终双库包括最后的18项新增交叉/故障/兼容回归。浏览器全套覆盖最终自动等待UI与SSE候选；其后仅后台旧缓存/技能兼容、工具图形覆盖元数据与模型输入完整性收尾，未重复浏览器全套，由最终双库及对应真实容器/格式回归覆盖。跳过项来自未配置的外部S3/OpenSandbox/真实后端等原有条件，不能当作这些后端已验。

新增等待查询已做SQLite/PostgreSQL EXPLAIN：SQLite使用`ix_runs_status_id/ix_message_refs_file`；PG从status索引进入，对小样本引用表选择顺序扫描的半连接，不强制索引、不声称大并发吞吐已验。没有新增无索引大表查询。

- 新增交叉回归覆盖四格式的纯图/夹图、真实parser输出、旧占位与缓存、在途复用、失败终止、页范围校验、暂停恢复不重复模型/沙箱、SSE/准入/取消，以及旧parser升级的OCR私有复制。
- 已完成真实离线容器的四格式含图读取与LibreOffice/Poppler渲染，并完成系统reader两并发四格式合同。没有用Mock沙箱结果冒充容器验证。
- 没有更改个人部署、账号、业务库、文件状态或模型配置；未调用付费LLM。读取原指定PDF作隔离验证只读，OCR执行器真实识别与业务任务状态回归分开记录。真实视觉模型答案质量、NPU和数百人容量不在本轮已验范围。

验证期间修正了测试启动目录/GNU工具PATH、临时PostgreSQL的UTF-8编码，以及实际发现的Excel绘图读取和PG JSON DISTINCT问题；不为这些失败放宽断言。最终结果只计正确环境、修正后通过的检查。

## 性能与真实样本

- 原指定900109字节、两页PDF只读导出，在无网络、512MiB/1CPU的临时容器验证：两页均为no_text且visual=true，原生解析约448ms、进程峰值RSS约139MiB，解析＋两页渲染约0.80秒（不含容器创建/上传扫描）。真实现用CPU RapidOCR分别约2.56/2.27秒，发票金额与备忘录主题断言通过；未触发付费LLM、未写原业务库。
- 四格式纯图/含图临时容器样本：PDF/DOCX/PPTX/XLSX分别渲染1/1/1/2页，进程峰值RSS约111/91/78/79MiB；Excel临时文字包不含绘图，但原文件页面仍实际渲染成功。
- 系统reader两并发四格式端到端约3.40–4.64秒，阶段计时显示创建/准入约1.84–2.39秒、执行约0.46–0.91秒、导出约0.29–0.30秒、释放约0.74–1.19秒；注入约0.07–0.26秒包含在创建/准入内，不能再相加。样本不同于原用户首次上传，不将其当作18秒到4秒的等价提速证明。
- 主要确定性节省是直接读取已存在OCR、避免重复渲染/推理和模型轮询、等待不持有用户沙箱/执行槽；仍有首次隔离环境启动开销。未引入跨用户常驻reader池或额外依赖服务。

## 下一步

本地修复和验证完成后交由用户决定发布；不因本轮授权自动部署。上线时涉及API、Agent、Worker、Portal、系统reader与Work Office runtime的对应源码，不能只换Agent而仍运行旧parser；本机测试部署规则保持只明确要求才部署。

## 发布授权

用户后续已明确授权部署最新版、提交PR及merge；当前开始发布。复用本轮通过的检查，CI按既定流程运行；部署不备份、不额外执行浏览器/模型/功能验收。最终PR/部署结果在此节补记。
