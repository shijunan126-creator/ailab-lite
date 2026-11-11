# 办公技能的工具面与残留清单（2026-11-11）

审查对象：`vendor/office-suite/`（固定上游 `a60ba6a7`）与它生成的 `images/sbx-office/skills/`，加上 `platform/ailab_platform/agent/` 的 `tools.py`、`prompts.py`、`discovery.py`、`office_guides_v2.json`。

基线 `origin/main@bc5c5b5c`。「已修」的三项在分支 `fix/office-skill-residue`；其余是待决定的事实记录，不代表已同意实施。

## 结论

1. **技能里没有 ZCode 自有工具名。** `Read`/`Bash`/`Task`/`Glob`/`Grep`/`WebFetch`、`open_document`/`read_document`/`search_document`/`show_in_viewer`/`get_view`/`capture_view`、`::zcode-file-citation`、`AskUserQuestion`、`ZCODE_*`、`tools: [Read, Bash]` 全部被 `vendor/office-suite/patches/001–013` 改写；`tests/test_office_suite_resources.py:63` 断言生成物不含 `zcode`、`account:`、`GLM`。
2. **那个断言只查 `zcode`**，漏掉上游厂商域名 `z-cdn.chatglm.cn`：它还留在 2 份 `env_setup/setup.md`（共 6 处）和几份已被中和的安装脚本里（见「未修残留」R5）。
3. **平台工具名只出现在三处**：顶层导航 `platform.*/SKILL.md`、`platform.*/viewer/SKILL.md`、`platform.*/resources/SKILL.md` 的交付引用段。技能的大块正文（`routes/` `scenes/` `briefs/` `references/`）不点名任何平台工具，只给 `pandoc`/`unzip`/LibreOffice/python 库的代码。
4. **同一份包里 v1 与 v2 名字并存是设计，不是冲突**：v1 文本冻结给旧 run，v2 由 `office_guides_v2.json` 覆盖导航与 viewer。

## 工具目录现状（v2，新 run 默认）

`api/routes_runs.py:200` 对新 run 默认 `document_tools_version=2`；取舍由 `domain/document_tools.py::permitted()` 决定。下表是 web 关闭、portal 客户端、视觉模型下的实测结果：

| 模式 | 数量 | 工具 |
|---|---|---|
| Work | 24 | ask_user, copy_library_file, deliver_file, exec, exec_kill, exec_wait, list_files, load_skill, memory, ocr_workspace_image, read_file, recall_history, resolve_attachment, review_artifact, run_js, run_subtasks, save_skill, schedule, search_images, search_library, ui_components, update_plan, view_workspace_file, write_file |
| Chat | 12 | load_skill, memory, read_document, recall_history, resolve_attachment, run_js, save_skill, schedule, search_document, search_library, ui_components, view_document |

会随部署或模型变化的：

- `web.enabled()` 打开时加 `web_search`/`read_url`/`download_url`/`fetch_repo`/`fetch_packages`；
- `search_capabilities` 由 `agent/loop.py:99` 单独加入；
- `run_js` 只在 `client == "portal"`；`ui_components` 只在 GenUI 模型；
- `view_workspace_file`/`view_document`/`view_attachment` 只在视觉模型。

**关键不对称**：Work 没有 `read_document`/`search_document`/`view_document`。Work 读文档是 `office-read SOURCE NEW_OUTPUT_DIRECTORY` → 看 `manifest.json` → `read_file` 读编号块，每次都要一个不存在的新目录。

## 技能里出现的名字

| 名字 | 出现位置 | 性质 |
|---|---|---|
| `office-read` | 4 份导航 + 4 份 viewer | 沙箱 CLI（`/usr/local/bin/office-read` → `runner.mjs`），不是模型工具 |
| `exec` / `exec_wait` | 4 份导航 | 平台工具 |
| `deliver_file` | 4 份导航 + 4 份 `resources/SKILL.md` 的引用段 | 平台工具 |
| `copy_library_file` | 4 份导航 + `resources/SKILL.md` | 平台工具 |
| `view_workspace_file` / `review_artifact` | `visual-judge.md`、pptx `resources/SKILL.md` | 平台工具 |
| `ocr_workspace_image` | 仅 V2 覆盖层 | 平台工具，只 Work v2 有 |
| `resolve_attachment` / `search_library` | 4 份导航 + 4 份 viewer | 平台工具，v1/v2 都有 |
| `read_library_file` / `view_attachment` / `extract_text` | 4 份导航 + 4 份 viewer | **v1 工具**，v2 已剔除 |
| `document_view` | 仅 4 份 `viewer/SKILL.md`（43 次） | **v1 工具**，v2 已剔除 |
| `read_document` / `view_document` | 仅 V2 覆盖层；viewer 里的 `read_document` 是「没有这个独立工具」的说明句 | 平台工具，只 Chat v2 有 |
| `load_skill` / `save_skill` | 导航、`auto-skill` | 平台工具 |
| `run_subtasks` | `visual-judge.md`、pptx `resources/SKILL.md` | 平台工具（本分支新加） |

`exec_kill`、`read_file`、`write_file`、`list_files`、`ask_user`、`update_plan`、`web_search`、`read_url`、`download_url`、`fetch_repo`、`fetch_packages`、`memory`、`recall_history`、`run_js`、`search_images`、`ui_components`、`schedule` **在技能里一次都没出现**；`read_file` 只出现在 `prompts.py` 的「外部技能用 read_file 阅读」一句里。

## v1 冻结 vs v2 覆盖

`agent/skill_modules.py::read_module` 的行为决定同一份包里两种名字共存：

| 读到的东西 | v1 run | v2 run |
|---|---|---|
| `load_skill(name="platform.docx")` | 读 `SKILL.md` 原文（含 `read_library_file`/`view_attachment`/`document_view`） | 返回 `office_guides_v2.json` 的 `navigation`（`office-read`/`view_workspace_file`/`exec`/`ocr_workspace_image`/`copy_library_file`/`deliver_file`） |
| `load_skill(resource="viewer/SKILL.md")` | 读原文（`document_view` + `read_library_file`） | 返回 `office_guides_v2.json` 的 `viewer`（`read_document`/`view_document`，并明确「没有 open_document/show_in_viewer/get_view/capture_view/read_document 这些独立工具」） |
| `load_skill(resource="routes/*.md" 等)` | 原文 | 原文（仅 `pdf briefs/process-advanced.md`、`xlsx engines/design.md` 有覆盖） |

所以「v1 viewer 里 `read_library_file` 和 `document_view` 混用」不是缺陷：v1 里两个工具都存在，且 v2 完全不走这份文本。

## 已修（分支 `fix/office-skill-residue`，commit `8eb9d71d`）

新增补丁 `vendor/office-suite/patches/013-tool-name-residue.json`：

| 位置 | 原问题 |
|---|---|
| 4 × `agents/visual-judge.md` 描述 | `spawn visual-judge`（AILab 没有按名字生成子代理的能力）、`dispatch message`、`planned:P2 · 来源交付协议未提供` |
| `platform.pptx/resources/SKILL.md:155` | `use the visual-judge subagent` |
| `platform.pdf/briefs/creative.md:630` | `ask the user via user clarification`（不存在的工具名，且让模型去装 Playwright） |

**其中 visual-judge 那句是判断，不是事实修正，且我第一次写错了**：`run_subtasks` 的 schema 是 `minItems: 2`，所以「交给一个子任务」不成立；平台的默认视觉门禁也不是子任务，而是常驻的 `review_artifact`。正确的主次是「先用 `review_artifact`/`view_workspace_file` 自己逐页判定，长成品再拆给 2–4 个子任务」，待确认后再改。

## 未修残留

### R1 三份 `env_setup/setup.md`（work 可读）

| 文件 | 行数 | tokens | 仍保留 |
|---|---|---|---|
| `platform.docx/env_setup/setup.md` | 276 | 2971 | `choco install nodejs-lts python3`、`scoop install …`（62–63 行）；字体安装（66–133）；国内镜像与安装（134 行起） |
| `platform.pdf/env_setup/setup.md` | 302 | 3314 | 同上，另有 `PLAYWRIGHT_DOWNLOAD_HOST=… npx playwright install chromium`（168 行） |
| `platform.xlsx/env_setup/setup.md` | 257 | 2706 | 表 135–136 镜像、145 行起安装说明 |

这些文件由补丁 002/006 把**段落**换成了「依赖预装、缺失即停」，但**表格和命令块留了下来**，于是同一文件里两套相反的指令并存。

### R2 `platform.pptx/resources/SKILL.md` 的 `## Dependencies`（654–703 行）

51 行里包含 `tar -xzf … && sudo dpkg -i …`、`hdiutil attach …`、`msiexec /i …`、`setx PATH …`、Tsinghua 镜像表，以及 7 次重复的「Dependencies must be preinstalled」占位段落。

### R3 `env_setup/env_check.sh` 会在工具输出里打印安装指令

`platform.docx/env_setup/env_check.sh:13`（pdf `:17`、xlsx `:13`）的 `required_on_demand` 在缺 `soffice` 时打印：`you MUST install it … preferred source: https://mirrors.tuna.tsinghua.edu.cn/… then register it on PATH (ln -sf / setx)`。文件可执行、也在导航里，比 `.md` 更容易出现在上下文。实际镜像装了 LibreOffice，所以正常路径不触发。

### R4 安装脚本已中和，但测试只覆盖 `.sh`

`setup*.sh` 第 2–4 行是 `echo … && exit 1`；`setup_windows.ps1` 第 1 行是 `throw "Dependencies must be preinstalled…"`。测试 `tests/test_office_suite_resources.py:67` 只断言 `setup*.sh`，`.ps1` 的中和没有断言，改坏不会被发现。

### R5 `z-cdn.chatglm.cn`（上游厂商 CDN）

6 处（2 份 `.md`）：`platform.docx/env_setup/setup.md:70/108/118`、`platform.pdf/env_setup/setup.md:89/127/137`；另有 `setup_mac_linux.sh`、`setup_windows.ps1` 的 `FONT_CDN_BASE`（已中和）。沙箱无网络，这些地址永远不可达，属于厂商残留而不只是失效指令。

### R6 `visual-judge.md` frontmatter

仍保留上游的 `color: yellow` 和 `tools: [view_workspace_file]`。AILab 不解析资源文件 frontmatter，无功能影响；`model:` 和 `thoughtLevel:` 在补丁 001 里已经删掉。

## 与 Pi 的 read / bash / edit / write 对照

| Pi | AILab 现状 | 差距 |
|---|---|---|
| `read` | `read_file(path, offset, limit)`，窗口 30 000 字符（`tools.py:691`），**不返回行号**，二进制直接拒绝 | 无行号；`edit` 的定位信息缺失 |
| `bash` | `exec(cmd, yield_seconds≤120，默认 30)` → 句柄；`exec_wait(handle, seconds, offset)`；`exec_kill`；单命令 ≤10 分钟；后台进程随租约存活 | 基本等价 |
| `write` | `write_file(path, content)`，整份覆盖，UTF-8，无大小上限 | 等价 |
| `edit` | **不存在** | 改文本只能 `read_file` + 整份 `write_file`；改 Office 文件本来就是写脚本重新生成（`routes/edit.md`） |
| `grep` / `find` / `ls` | 无独立工具；`list_files` 只列一层、上限 500；镜像里没有 ripgrep（apt 清单无，`grep`/`find` 为 Debian 基础包） | 搜内容/找路径只能 `exec` 里 `grep`/`find`，靠模型自己拼命令 |

`run_subtasks` 子任务可用：list_files, read_file, write_file, exec, exec_wait, exec_kill, search_library, resolve_attachment, copy_library_file, view_workspace_file, ocr_workspace_image, web_search, read_url, search_images, recall_history；不能交付、提问、再派子任务。

## 若要对齐 Pi，需要动的位置

- `vendor/office-suite/build.py`：`navigation()`、`document_guide()`、`viewer_guide()` 生成技能正文与 `office_guides_v2.json`，`reader/viewer` 名字在这里按模式选。
- `vendor/office-suite/patches/*.json`：上游文本改写清单，改后要重算两侧哈希与偏移。
- `platform/ailab_platform/agent/tools.py`：schema、handler、`SANDBOX_TOOLS`、`_current_recall`。
- `platform/ailab_platform/agent/prompts.py`：`BASE_PROMPT`、`CHAT_MODE_NOTE`、文档引用段；有锚点断言（替换文本缺失即 `ValueError`）。
- `platform/ailab_platform/agent/discovery.py`：`REDUCED_V1`/`REDUCED_V2` 预算裁剪名单。
- `platform/ailab_platform/domain/document_tools.py`：`permitted()` 的 v1/v2 门禁。
- `platform/ailab_platform/metrics.py:93`：`KNOWN_TOOLS`。
- `platform/ailab_platform/agent/office_guides_v2.json`（生成物，不手改）。
- 工具目录是请求前缀的一部分（ADR 0030）：改名字等于新的 tool catalog 版本，要记 `agent.context_boundary` 并复测 `cached_tokens / prompt_tokens`。

## 未验证

- 没有在真实镜像里跑过这些技能（本轮只改文本，未重建 sbx-office 镜像）。
- 未跑完整双库套件、Portal、E2E、真模型；本轮只跑了 office 资源与技能相关的 6 个测试文件（106 passed）。
- 「模型读到 setup.md 后会去执行安装」是推断，没有用真模型复现。

## 待确认

1. 残留清理范围：只清 R1+R2（验证清单化），还是连 R3/R5 一起（把 `chatglm.cn` 和镜像表清成 0 命中）？R4 的测试缺口补不补？
2. `visual-judge` 那句是否按「先自己用 `review_artifact` 判定，长成品再拆 2–4 个子任务」改正？
3. 对齐 Pi 的方案范围：只补 `edit` + 打开 `grep`/`find`，还是把 `read_file`/`write_file` 也改名成 `read`/`write`（改名会改变工具目录与前缀缓存边界）。
