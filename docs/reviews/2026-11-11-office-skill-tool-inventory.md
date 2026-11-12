# 办公技能的工具面与残留清单（2026-11-11）

审查对象：`vendor/office-suite/`（固定上游 `a60ba6a7`）与它生成的 `images/sbx-office/skills/`，加上 `platform/ailab_platform/agent/` 的 `tools.py`、`prompts.py`、`discovery.py`、`office_guides_v2.json`。

基线 `origin/main@bc5c5b5c`；工具名已按 PR #188（`d6651ce2`，工作区工具对齐 Pi）更新到 `main@99602a5a`。2026-11-12 复核的新发现与修复见[复核记录](../progress/updates/2026-11-12-office-skill-recheck.md)。

## 结论

1. **技能里没有 ZCode 自有工具名。** `Read`/`Bash`/`Task`/`Glob`/`Grep`/`WebFetch`、`open_document`/`read_document`/`search_document`/`show_in_viewer`/`get_view`/`capture_view`、`::zcode-file-citation`、`AskUserQuestion`、`ZCODE_*`、`tools: [Read, Bash]` 全部被 `vendor/office-suite/patches/001–013` 改写；`tests/test_office_suite_resources.py:63` 断言生成物不含 `zcode`、`account:`、`GLM`。
2. **那个断言只查 `zcode`**，漏掉上游厂商域名 `z-cdn.chatglm.cn`：它还留在 2 份 `env_setup/setup.md`（共 6 处）和几份已被中和的安装脚本里（见「未修残留」R5）。
3. **平台工具名只出现在三处**：顶层导航 `platform.*/SKILL.md`、`platform.*/viewer/SKILL.md`、`platform.*/resources/SKILL.md` 的交付引用段。技能的大块正文（`routes/` `scenes/` `briefs/` `references/`）不点名任何平台工具，只给 `pandoc`/`unzip`/LibreOffice/python 库的代码。
4. **同一份包里 v1 与 v2 名字并存是设计，不是冲突**：v1 文本冻结给旧 run，v2 由 `office_guides_v2.json` 覆盖导航与 viewer。

## 工具目录现状（v2，新 run 默认）

`api/routes_runs.py:200` 对新 run 默认 `document_tools_version=2`；取舍由 `domain/document_tools.py::permitted()` 决定。下表是 web 关闭、portal 客户端、视觉模型下的实测结果：

| 模式 | 数量 | 工具 |
|---|---|---|
| Work | 27 | ask_user, bash, bash_kill, bash_wait, copy_library_file, deliver_file, edit, find, grep, load_skill, ls, memory, ocr_workspace_image, read, recall_history, resolve_attachment, review_artifact, run_js, run_subtasks, save_skill, schedule, search_images, search_library, ui_components, update_plan, view_workspace_file, write |
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
| `bash` / `bash_wait` | 4 份导航（v1 导航写 `bash(command=...)`） | 平台工具 |
| `deliver_file` | 4 份导航 + 4 份 `resources/SKILL.md` 的引用段 | 平台工具 |
| `copy_library_file` | 4 份导航 + `resources/SKILL.md` | 平台工具 |
| `view_workspace_file` / `review_artifact` | `visual-judge.md`、pptx `resources/SKILL.md` | 平台工具 |
| `ocr_workspace_image` | 仅 V2 覆盖层 | 平台工具，只 Work v2 有 |
| `resolve_attachment` / `search_library` | 4 份导航 + 4 份 viewer | 平台工具，v1/v2 都有 |
| `read_library_file` / `view_attachment` / `extract_text` | 4 份导航 + 4 份 viewer | **v1 工具**，v2 已剔除 |
| `document_view` | 仅 4 份 `viewer/SKILL.md`（43 次） | **v1 工具**，v2 已剔除 |
| `read_document` / `view_document` | 仅 V2 覆盖层；viewer 里的 `read_document` 是「没有这个独立工具」的说明句 | 平台工具，只 Chat v2 有 |
| `load_skill` / `save_skill` | 导航、`auto-skill` | 平台工具 |

`bash_kill`、`read`、`write`、`edit`、`ls`、`grep`、`find`、`ask_user`、`update_plan`、`web_search`、`read_url`、`download_url`、`fetch_repo`、`fetch_packages`、`memory`、`recall_history`、`run_js`、`search_images`、`ui_components`、`schedule` **在技能里一次都没出现**；`read_file` 只出现在 `prompts.py` 的「外部技能用 read_file 阅读」一句里。

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

visual-judge 那句第一版写成「交给一个子任务」，不成立（`run_subtasks` 是 2–4 个，平台默认门禁是 `review_artifact`）。最终版（2026-11-13）：每个成品由模型自己对 `review_artifact` 返回的页面图判定一次，技能里不再提子任务——Agent 默认不派子任务，用户明确要求时才派。

## 残留 R1–R6（2026-11-13 已全部修复）

| 编号 | 原问题 | 处理 |
|---|---|---|
| R1 | 三份 `env_setup/setup.md` 里 `choco`/`scoop`/字体下载/国内镜像命令，与「依赖预装」段落并存 | 补丁 014 重写为镜像实际依赖与字体表 |
| R2 | pptx `resources/SKILL.md` `## Dependencies` 的 `sudo dpkg`/`hdiutil`/`msiexec`/`setx`/镜像表 | 缩成一段：依赖预装，缺失即报告并停止 |
| R3 | `env_check.sh` 缺依赖时打印「MUST install」与清华镜像 | 改为「环境故障，报告并停止，不安装不替代」 |
| R4 | 测试只断言 `setup*.sh` | 测试同时断言 `.sh` 只剩 4 行、`.ps1` 只剩一行 `throw` |
| R5 | `z-cdn.chatglm.cn` 字体 CDN | 随 setup 文档与脚本删除；新测试 `test_generated_resources_only_promise_what_the_image_has` 拦住回流 |
| R6 | `visual-judge.md` frontmatter 的 `color`/`tools` | 删除，内容并入描述 |

镜像依赖、字体、品牌等其余发现与修复见[复核记录](../progress/updates/2026-11-12-office-skill-recheck.md)。

## 与 Pi 工具的对照

已由 PR #188 实施：`read`/`write`/`edit`/`ls`/`grep`/`find`/`bash`/`bash_wait`/`bash_kill`，旧名见 `agent/tools.py::RETIRED_TOOLS`。子任务可用的工具以 `agent/subtasks.py::allowed()` 为准（不含 `load_skill`、`review_artifact`、`deliver_file`）。

## 未验证

- 没有在真实镜像里跑过这些技能（本轮只改文本，未重建 sbx-office 镜像）。
- 未跑完整双库套件、Portal、E2E、真模型；本轮只跑了 office 资源与技能相关的 6 个测试文件（106 passed）。
- 「模型读到 setup.md 后会去执行安装」是推断，没有用真模型复现。

## 待确认

1. 残留清理范围：R1/R2/R5（setup 文档的安装说明与厂商 CDN）仍未清；R3 与 pdf `env.check` 的安装提示已在 2026-11-12 改成「报告并停止」。
2. 字体与镜像依赖对齐（2026-11-12 新发现，见复核记录）单独开 PR。
