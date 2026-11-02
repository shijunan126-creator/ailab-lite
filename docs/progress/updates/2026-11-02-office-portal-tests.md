# Office Portal 测试（2026-11-02）

实施基线 `feat/office-suite-portal@7d226ab`，共同 base `origin/main@bf0af4f`。测试提交 `af203ff`；最终检查基线为本记录所在修复提交的产品/测试树（父提交 `af203ff`）。范围仅 Portal 测试与真实前端缺陷修复，无 push/PR/部署，其他 worktree 未修改。接口依据 [04 合同](../../architecture/design/office-suite/04-viewer-and-file-contract.md)。

## 自动检查

新增23项 Vitest 覆盖 channel/source/nonce/长度，load_document 路径围栏、字节 token 单次消费及 transfer、文件/工作区引用与 ask、未知调用、启动失败回退；索引 pending 轮询与取消、native/大小/失败回退；引用最大8条/去重/按 key 清理；outbox 可选 doc_refs；截图按本页 view 认领、超限缩窄、关闭错误及原 JS 路径；标准字体 url 部分替换。

- 最终 `npm test`：34文件、161 passed，exit 0，7.29秒。
- `OFFICE_RUNTIME_SRC=<runtime源码目录> npm run build`：exit 0，viewer 4.05 MiB，Next 编译与类型检查通过；独立 `npx tsc --noEmit` exit 0。
- 最初159项测试通过后，构建暴露新增测试的类型错误，已修正并重跑受影响3文件18项；截图缺陷回归在修复前1 failed / 11 passed，修复后通过，最终整套结果以上述161项为准。未把这些中间检查当成多轮独立验收。

## 真实缺陷与修复

父消息桥把所有响应都限制为256 KiB字符，合法PNG base64超过该值就被静默丢弃，截图等待超时，后续2 MiB结果预算的缩窄重拍也无法执行。回归从 `captureView` 发起实际父桥请求，再发送256 KiB以上的图片响应：旧代码得到 dropped，修复后得到完整图片及定位/版本。

截图响应使用独立16 MiB字符传输上限；普通控制消息保持256 KiB，并把 boot/error 纳入长度守卫。截图最终提交仍沿用2 MiB×4/3的base64限制及逐次缩窄。只改消息准入，无新增后台任务、依赖或网络请求；图片响应仍有一次JSON序列化临时字符串，16 MiB传输上限不代表最终消息或内存预算扩大。未测超过传输上限的大截图、长时间RSS/CPU或生产容量。

## 浏览器结果

内置浏览器打开 `http://localhost:3917/office-test`；选区和矩阵使用 Playwright Chromium，真实 `page.mouse` 按 frame 内元素 boundingBox 的页面坐标操作，无脚本伪造选择事件。以用户提供的未提交页面 fetch shim 与 runtime 的四个fixture验证；本页proxy只检查cookie存在，Playwright使用隔离测试cookie，不把它视为真实API登录或权限验收。

| 项目 | 结果及关键证据 |
| --- | --- |
| 四格式渲染 | PASS：report.docx 两页正文与中文；budget.xlsx Revenue/Notes与数值；launch.pptx三页标题/时间线/预算；cjk.pdf四行中文正常显示 |
| 浅色/深色 | PASS：系统颜色偏好切换同步父html.dark和frame data-theme；无需重载文档 |
| 手机 | PASS：390×844四格式各浅/深色；与1280×800桌面合计16组；frame scrollWidth与clientWidth一致。手工页顶部测试按钮横排超宽，不据此评价实际Portal导航 |
| reveal | PASS：DOCX p:3、XLSX Revenue!A1:B3、PPTX slide:2、PDF page:1均高亮并出现“看这里”；XLSX高亮由canvas绘制，不能用DOM class数量判断 |
| capture | PASS：四次日志分别1600×2071、1600×859、1600×1200、1600×2264；对应base64字符231584、129504、91212、230860。真实fixture均小于256 KiB，较大响应和2 MiB重拍由自动回归覆盖 |
| DOCX选区→引用 | PASS：鼠标拖选正文后点击“添加到对话”；日志 `ref report.docx · p:2`，quote为 `This report summarises revenue for the third`，精确file_id/version |
| XLSX选区→引用 | PASS：鼠标拖选A1:C3后点击“添加”；日志 `ref budget.xlsx · Revenue!A1:C3`，quote为三行TSV：Region/Q1/Q2、North/100/110、South/80/90 |
| ask | PASS：上述XLSX选区中输入“这些数字说明什么？”并发送，日志ask包含同一Revenue!A1:C3及对应引文 |

矩阵和交互检查均无pageerror。中间一次选择器等待超时来自误把XLSX“添加”按钮写成DOCX的“添加到对话”，修正定位后通过，无产品修改。

手工页缺少DOCX blocks时，文本可拖选但无法映射at；已在未提交页补fixture索引，并把虚构Sheet1更正为Revenue。每次viewer build后重新复制四fixture到忽略的office/dist。手工页始终未提交，原始日志/截图不进仓库；截图已删除、dev server及自建浏览器已停止。

## 未验与下一步

未运行Python/Redis/PostgreSQL/E2E套件（无后端改动，本轮明确为Portal测试子任务）；未跑真实模型、后端doc_refs授权/版本校验、真实索引生成、完整聊天发送/排队/分享链路、Safari或物理手机。fixture回调通过不等同这些合同通过。下一步由主任务整合P3/P4并进行授权范围内的联合验收；本子任务不发布。
