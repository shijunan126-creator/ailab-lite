# 提取文字内存与恢复修复

## 范围与基线

用户授权定位并修复；基线`origin/main@3d85a86e589cb651b954b8feba11f46e1203237d`，独立分支`fix/ocr-memory-recovery`。仅本地实现/验证，无push、PR、merge或部署。主工作区既有修改保持。运行环境身份与用户文件只留私有环境记录，不入仓库。

## 根因与修改

- 旧执行器无条件为三份ORT session开启节点profiling。ORT在session生存期内累计事件，常驻服务不结束session；内存随文字批次/请求数上涨。实际图片2013×731，检测输入1×3×736×1984，检测阶段没有OOM；识别固定6×3×48×320批次时持续上涨，最终触发容器1GiB限制。历史简单样本成功不代表密集文字或常驻稳定性。
- 默认关闭profiling；保留现有`OCR_PROFILE_PREFIX`作为显式、短时隔离诊断入口。模型、检测尺寸、识别批量、图片清晰度、线程、CPU/内存限制及派生fingerprint均不改；CANN禁CPU回退的session配置保持。
- Worker捕获OCR依赖/进度异常后仅return，durable claim仍占用660秒；改为通过既有`retry_after(2)`释放租约，由outbox重新投递同一身份。已持久页不重复发布；文件READY状态保持。

## 验证

产品/回归提交`b0f0dfd46493b94eaf0fe29fbce2474c70cb322c`；完整SQLite结果如下。产品代码自该轮测试启动后未改；新增测试的时区/上游故障夹具修订另行复核，不能把阶段失败省略。

- 完整SQLite `PYTHONPATH=<本检出>/platform .venv/bin/python -m pytest tests -q -ra`：2341通过、205跳过、4失败。四项均为background_exec测试命令中的`sys.executable`位于`/private/tmp`，被Mock沙箱的`/tmp`映射改写为不存在的解释器路径；保持本检出PYTHONPATH，改用工作区外现有venv解释器后，仅这4项复核全部通过。未修改产品或放宽断言，未重复整套。205跳过包括未配置外部后端及未构建viewer reader等环境边界，不算通过。
- `ruff check platform images/rapidocr/executor.py`、`ruff format --check ...`通过；Portal `npm ci --ignore-scripts && npm run build`通过。
- SQLite专项78项通过；其后上游503与时区断言修订，最终OCR整组15项通过。PostgreSQL专项初轮77过/1失败：测试把已有Asia/Hong_Kong偏移强制replace为UTC，错误偏移8小时；只对无时区的SQLite值补UTC，保留PG真实偏移后，该失败用例单独通过。产品释放租约在两库均成功；没有重跑已通过77项。
- 同一RapidOCR/ORT CPU镜像、模型与输入：旧代码1GiB复现OOM；仅为测完整输出的隔离旧代码容器放宽至2GiB，一次识别峰值1234.40MiB。修复代码在原1GiB/0.75CPU下连续10次成功，cgroup峰值454.28MiB、后续趋稳，单次中位16.76s；每次326行/2176字，文本SHA完全一致，也与旧代码放宽内存后的输出一致。不缩图、不改batch/模型、不开CPU arena，无新增依赖或后台job；失败路径增加一次已有release HTTP请求。测试CPU为共享本机环境，不作吞吐SLA。
- 真实HTTP执行器→API→Worker→私有OCR缓存/正文下载通过：合成中文编号/金额/日期精确，独立副本缓存不重复推理，删除原件不影响副本，付费模型调用0。HTTP测试容器峰值543.89MiB，OOM计数0。该验收脚本原先默认上传会复用同一文件身份，不能证明私有副本；已显式指定`disposition=copy`。首次字体路径不存在，在真实读取现有字体后继续；不把准备失败算产品回归。

## 边界与下一步

未部署，未push/PR/merge；运行服务仍为旧版本。未改UI，因此不另跑UI浏览器；未触及模型请求构造，无真模型付费调用。实际NPU/CANN硬件、30分钟常驻压力、任意文档精度、完整PG/Redis与整套E2E未验；本轮证据为CPU同图重复、真实HTTP链路与双库任务恢复。

三个临时OCR容器已删除，临时PostgreSQL已停止，未动用户业务数据或运行服务。原始图、逐批日志、追踪输出、临时库及凭据均已从仓库外调试目录清理；仓库仅保留结论与必要回归。下一步按用户后续指令发布，部署只需必要共享后端/独立OCR代码增量更新，不沿用旧部署授权。
