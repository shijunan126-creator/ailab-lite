# PPT 模板真实首屏缩略图（2026-10-24）

## 范围与状态

- 分支 `feat/ppt-templates-backend`，本轮起点 `833f64b`，实现提交 `710eba3`，测试基线 `771d9fe`。本页之后仅更新文档，不改变运行代码。
- 父任务报告初版已合入 `feat/ppt-templates-portal@1c53b46` 并验收公司/个人/one-off、冻结和turn chip；这是父任务证据，不是本轮重新执行的Portal验收，也不代表合入main或部署。
- 本轮只实现真实首屏缩略图；未修改 `platform/portal/`，未push/PR/merge/deploy。数据库、迁移、JobEnvelope和队列代码无改动。

## 行为

- 复用资料库Office预览的 `sandbox_tools/render_preview.pages`：LibreOffice仅导出第一页PDF，Poppler仅光栅化第一页，宽480px，生成JPEG。真实页保留背景、布局和图像，不再把草图作为首选。
- `.potx`直接交给Office；零页文件尝试其可导出页面，若无页面/失败则读取首layout作草图。零页PPTX、零页POTX、有页POTX均有回归。
- 分析与缩略图为独立受限操作。渲染失败、缺工具、超时、OOM后，在另一个受限进程绘制草图；草图也失败时，保留成功分析和ready状态，thumbnail_url为null。派生对象上传失败也不会把模板置failed。
- 新对象为 `derived/{file_id}/template-v2.jpg`，响应 `image/jpeg`、private immutable缓存及v2 ETag；ready文件不再处理，已写入v2对象可供重投直接复用。旧v1 WebP仍能读取，未批量重新渲染历史缓存。

## 时间、资源和镜像

- 原 `parse_timeout_seconds` 总wall预算按85%/15%分配，默认渲染51秒、草图9秒；未提高默认192MiB、配置上限256MiB、CPU45秒或16MiB输出预算。
- 渲染以内存预算相同的**整进程组RSS watchdog**约束，而非Linux虚拟地址空间（LO需要映射较大的共享库）；普通解析仍使用原RLIMIT_AS。RSS每轮约50ms采样，内存/超时触限杀整个进程组；父进程已退出时也清理残留子进程。
- 输出预算递归计入PDF、LO profile及JPEG；这些文件均位于Worker持有的任务临时目录，硬杀后仍由父目录清理，不依赖被杀进程的finally。Worker单消费者串行处理，未增加后台渲染并发或常驻LO进程。
- 资料库PPT原本通过Broker渲染，Worker镜像未带工具。本轮在共享Python镜像 `platform/deploy/Dockerfile` 补LibreOffice Impress、Poppler、CJK字体、fontconfig和procps。需要重建平台镜像；没有改Pod资源上限。镜像磁盘增量未实测，不能声称构建/部署已通过。

## 本机真实成本

命令（必须先载入隔离测试环境，避免读取开发者.env/DB）：

```bash
cd platform
PYTHONPATH=. /Users/michael/Developer/ailab-lite/platform/.venv/bin/python \
 -c 'import tests.conftest; from tests.measure_template_thumbnails import main; main()'
# 20页探针：同命令前加 AILAB_MEASURE_SLIDES=20
```

- macOS单页样本28,291字节，原有256MiB配置范围内三次均真实渲染成功：wall中位 **2.038068s**，包含Office/Poppler子进程的CPU中位 **1.771745s**，采样整进程组RSS峰值最大 **257,097,728字节（245.2MiB）**。Python子进程自身约23–24MiB不是总渲染RSS，未用它代替整树数字。
- 同样本默认192MiB三次均触发memory_limit（中位1.470574s）；正常完整管道自动回退草图，**1.646915s**完成并保持ready。**没有暗中提高默认内存；本机默认配置下不能声称总能展示真实首屏。**
- 本轮先前20页45,231字节探针：256MiB下3次只有1次真实渲染成功（1.937979s，CPU1.718193s，采样RSS267,993,088字节），另2次触及内存限制；192MiB三次均fallback条件。该失败边界保留，不把小样本当所有模板/长期容量证明。
- 分析、扫描、网络、父Worker常驻RSS不包含在上述成功渲染CPU/RSS中；每模板正常只渲染一次，之后用immutable对象。未测重建后的Linux镜像成本；本机数据不能替代Linux默认192MiB真实验收。

## 验证

以下从本工作树platform目录执行，使用复用的Python3.12解释器、`PYTHONPATH=.`及测试conftest的临时env/数据目录；未使用开发者.env或数据库。

```bash
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/findutils/libexec/gnubin:$PATH"
PY=/Users/michael/Developer/ailab-lite/platform/.venv/bin/python
/Users/michael/Developer/ailab-lite/platform/.venv/bin/ruff check .
/Users/michael/Developer/ailab-lite/platform/.venv/bin/ruff format --check .
PYTHONPATH=. "$PY" -m pytest -o addopts='' tests/test_template_thumbnails.py \
 tests/test_templates.py tests/test_templates_migration.py tests/test_scanner_isolation.py -q --tb=short
PYTHONPATH=. "$PY" -m pytest -o addopts='' tests -q --tb=short
```

- Ruff：check/format通过，329文件已格式化；`git diff --check`通过。
- 模板/缩略图/隔离专项：**52通过、1跳过，0失败，22.57s**。跳过为未设置临时PG URL的迁移参数；SQLite迁移照常通过。真实Office/Poppler测试实际执行，采用明确声明的256MiB配置；默认内存失败路径另有独立测试及实测。
- 最终全量SQLite：**1129通过、118跳过，0失败/错误，166.86s**。跳过为未配置PG/Redis、S3、真实容器/OIDC等外部条件；未把skip算成通过。
- 覆盖首屏选择/480px/真实背景像素、PDF导出PageRange和Poppler第一页、JPEG/ETag/304、只渲染一次/重投复用、旧WebP读取、timeout/OOM/缺工具fallback、双失败仍ready、进程组RSS/kill、父退出清理子进程和递归输出预算，以及原有模板全套。
- 本轮不改DB或队列代码，按父任务条件未重跑PG+Redis全套；也未重跑Portal/E2E。原后端双库/E2E结果只在[原交付](2026-10-24-ppt-templates-backend.md)作历史证据，不冒充本轮验证。
- Docker daemon不可用，未构建/运行新平台镜像；未部署。测试临时目录自动清理，未启动PG/Redis，完成后无本任务遗留LO渲染进程。

## 移交

父任务合入本轮提交后需重建平台镜像以包含Office/Poppler/procps；在目标Linux环境验证实际模板是否能在既有192MiB配置中真实渲染，超限仍fallback。若本机选择既有256MiB配置，仍须接受复杂模板可能触限的事实。旧v1缓存不自动重建；需要刷新已有测试模板时可重新上传。初版PPT生成器所需的sbx-office重建事项保持在原交付记录，本轮没有执行任何部署。
