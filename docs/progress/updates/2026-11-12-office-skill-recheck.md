# 办公技能复核：提示词、调用、依赖与跨部署读取（2026-11-12）

用户要求在 PR #188（工作区工具改名为 `bash`/`read`/`edit`/…）之后复查办公技能：提示词冲突、缺依赖、调用逻辑、前缀缓存，并核对分支 `fix/office-skill-residue` 已有记录。分支已 rebase 到 `main@99602a5a`；未推送、未部署。

## 对已有记录的核对

- [工具面清单](../../reviews/2026-11-11-office-skill-tool-inventory.md)写于 #188 之前，工具目录、技能里的工具名和"与 Pi 对照"一节已过期，已按 `main` 更正（Work v2 目录 27 个工具，实测 `tools_for_turn(2)`）。
- 残留 R1–R6 逐条复现属实（`sudo`/`dpkg`/`curl`/`z-cdn.chatglm.cn`/镜像源都还在）。
- 补丁 013 的 visual-judge 措辞记录里自认写错，属实：已改成「自己用 `review_artifact`/`view_workspace_file` 判定，长成品拆给 2–4 个 `run_subtasks`」。
- 013 把 pdf `creative.md` 改成「依赖缺失就报告并停止」，但 `env.check` 本身误报 Chromium 缺失（见下），两者叠加会让创意 PDF 路线在真实镜像里永远停下。本轮一起修。

## 新发现并已修

| 问题 | 根因 | 修复 |
|---|---|---|
| 部署更新技能包后，加载过办公技能的旧会话再也读不了该技能（导航和任何资源都返回 `installed skill package changed; rebuild context explicitly`），模型没有任何办法"重建" | 旧包不随镜像保留，校验却按 `loaded_skills` 里的旧哈希拒绝；#188 改了导航文本，所有部署前的会话都会碰到，且历史里留着 `exec执行` 的旧导航 | `load_skill` 读当前安装的包，按上下文里实际可见的同名模块判断是否过期，过期时在追加的结果里给一次说明（`SKILL_PACKAGE_UPDATED`），并更新 `loaded_skills`；历史不改写，不需要新的前缀边界。旧 `version` 参数给出当前版本号 |
| v1 导航写 `bash(cmd=...)` | #188 只改了工具名；参数已是 `command`（`cmd` 仅为兼容旧 checkpoint 接受） | `build.py` 改成 `bash(command=...)` |
| pdf `env.check` 报 Chromium 缺失 | 只查 `~/.cache/ms-playwright`；镜像用 `PLAYWRIGHT_CHROMIUM_PATH=/usr/bin/chromium`，渲染脚本本来认这个变量 | 补丁 014：探针先认 `PLAYWRIGHT_CHROMIUM_PATH` |
| 三份 `env_check.sh` 与 `pdf.py env.check` 在缺依赖时打印"必须安装、去清华镜像下载、`ln -sf`/`setx`"（R3）；pdf 的 `env_check.sh` 仍查 tectonic 并提示"按需安装" | 上游宿主安装流程，补丁 002/006 只改了段落 | 补丁 014：缺失即「环境故障，报告并停止，不安装不替代」；LaTeX 查 `xelatex` |

## 新发现、未修（需要单独 PR）

字体与系统命令和镜像不一致。下面这些已在 `python:3.12-slim-trixie` 里按镜像的 apt 清单模拟安装核对（不是完整镜像构建）：

- `pdf briefs/report.md` 的「Guaranteed」ReportLab 字体表（`msyh.ttf`、`SimHei.ttf`、`SarasaMonoSC`、`Times-New-Roman.ttf`、`calibri`、`DejaVuSansMono.ttf`）在镜像里一个都不存在；ReportLab 读不了 Noto CJK（CFF 轮廓），镜像里只有 `wqy-zenhei.ttc` 可用。这是 PDF 最常用的报告路线。
- `pdf briefs/resume.md` 的 FreeSerif、xlsx `engines/chart-templates.md` 的 `NotoSansSC-Regular.ttf`/`DejaVuSans.ttf` 同样不存在；`font_list.txt` 是上游自家镜像的字体清单。
- `gs`（`resources/SKILL.md` 的压缩 PDF）和 `qpdf` 命令行（`briefs/process-advanced.md` 的拆分/合并/优化）未安装；`--no-install-recommends` 下 ImageMagick 不带 Ghostscript。
- `briefs/academic.md` 仍说 Tectonic 会联网下载字体和宏包，实际是离线 XeLaTeX。

建议做法：开源可装的（`ghostscript`、`qpdf`、`fonts-dejavu-core`、`fonts-freefont-ttf`）进镜像，专有字体（雅黑/黑体/Times/Calibri）用补丁改成镜像里的字体并重建镜像实测渲染。R1/R2/R5（setup 文档）仍待清理范围决定。

## 检查过、没问题

- 系统提示词与技能导航的工具名一致（无 `exec`/`read_file` 等旧名）；子任务从父请求分叉，能看到父任务已读的技能正文，但自己不能 `load_skill`/`review_artifact`，visual-judge 措辞已按此写。
- Python/Node 依赖全部在 `requirements.lock` 与 Node 锁文件里；`soffice`/`pdftoppm`/`pandoc`/`xelatex`/`chromium`/`rg`/`fdfind` 在镜像清单里；四个 `scripts/check.py` 都存在。
- 前缀缓存：`load_skill` 结果只追加；本次修复不改历史、不改工具目录和 system，`tests/test_prefix_cache.py` 通过。

## 验证

- `ruff check`、`ruff format --check` 通过。
- 新增 `test_conversation_loaded_before_a_package_update_switches_to_the_installed_package`（v1/v2 × 版本号变/不变），去掉修复时失败。
- 技能/办公/前缀缓存/分叉相关 10 个测试文件 311 passed（本地未构建 `runner.mjs`）。
- `build.py --runtime-output` 重建可复现；`pdf.py env.check` 在设置 `PLAYWRIGHT_CHROMIUM_PATH` 时报 Chromium ✓。

未测：完整套件与 E2E（交给 CI）、真实 sbx-office 镜像里跑 `env_check.sh`、真模型多轮跨部署场景。部署时 Agent 与 sbx-office 镜像必须同时更新（`index.json` 变了）。
