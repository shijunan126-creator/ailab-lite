# 办公技能复核：提示词、调用、依赖与跨部署读取（2026-11-12 → 11-13）

用户要求在 PR #188（工作区工具改名为 `bash`/`read`/`edit`/…）之后复查办公技能：提示词冲突、缺依赖、调用逻辑、前缀缓存，并核对分支 `fix/office-skill-residue` 已有记录；第二轮要求「全部都修，都要发现问题」，并且 Agent 默认不派子任务，用户要求时才派；第三轮要求真模型验证、检查 Chat/Work 选中交付件段落是否有问题或影响缓存、固化代表性场景用例，然后提 PR、合并、部署。分支 rebase 到 `main@99602a5a`。

## 对已有记录的核对

- [工具面清单](../../reviews/2026-11-11-office-skill-tool-inventory.md)写于 #188 之前，工具目录、工具名和「与 Pi 对照」已按 `main` 更正（Work v2 目录 27 个工具，实测 `tools_for_turn(2)`）。
- 残留 R1–R6 逐条复现属实，本轮全部清掉（见下）。
- 补丁 013 的 visual-judge 措辞记录里自认写错，属实；最终改为「每个成品由你自己用 `review_artifact` 返回的页面图判定一次」，不再提子任务。

## 修了什么

| 问题 | 根因 | 修复 |
|---|---|---|
| 部署更新技能包后，加载过办公技能的旧会话再也读不了该技能（`installed skill package changed; rebuild context explicitly`，模型无路可走） | 旧包不随镜像保留，校验却按 `loaded_skills` 里的旧哈希拒绝；#188 改了导航文本，部署前的所有会话都会碰到 | `load_skill` 读当前包，按上下文里实际可见的同名模块判断过期，过期时在追加的结果里说明一次（`SKILL_PACKAGE_UPDATED`）并更新 `loaded_skills`；历史不改写，不新增前缀边界 |
| Agent 自己开子任务 | Work 提示词与 `run_subtasks` 描述鼓励「可拆就拆」，visual-judge 也让长成品拆给子任务 | 提示词改为「默认自己按顺序完成，用户明确要求并行或拆分时才用 run_subtasks」，工具描述同句开头；`PROMPT_VERSIONS["work"]` 升级（Work 会话一次性边界，Chat 不受影响） |
| v1 导航写 `bash(cmd=...)` | #188 只改了工具名 | `build.py` 改成 `bash(command=...)` |
| `pdf.py convert.html` 与 `convert.blueprint`（创意 PDF 主路线）直接失败：`html2pdf.js not found` | 上游从未发 `html2pdf.js`，只有替代它的 `html2pdf-next.js` | 补丁 014 改指 `html2pdf-next.js`；契约测试改为调用文档里的命令 |
| pdf `env.check` 报 Chromium 缺失，渲染脚本打印「⚠ fallback Chromium」 | 只查 `~/.cache/ms-playwright`；镜像有意用 `PLAYWRIGHT_CHROMIUM_PATH=/usr/bin/chromium` | 探针先认该变量；提示改为普通的「Using Chromium」 |
| 缺依赖时打印「必须安装、清华镜像、`sudo`、`ln -sf`/`setx`」；setup 脚本/文档是上游宿主安装流程（R1–R3、R5） | 上游按自家机器写 | `env_check.sh`/`pdf.py`：缺失即环境故障，报告并停止，不安装不替代；setup 脚本只剩报告退出；`setup.md` 改成镜像实际依赖与字体表；`font_list.txt` 删掉上游清单 |
| PDF 报告路线「Guaranteed」字体表（`msyh.ttf`/`SimHei.ttf`/`Times-New-Roman.ttf`/`calibri`…）在镜像里一个都不存在；ReportLab 读不了 Noto CJK（CFF 轮廓） | 上游自家镜像的路径 | 注册名保留，文件改成镜像里能嵌入的：`wqy-zenhei.ttc` 子字体 0/1、Liberation Serif、Carlito、DejaVu Sans Mono；resume/图表模板/FAQ 同步 |
| 封面 `cover_render` 宋体/楷体落到 fontconfig 随便挑的字体，西文落到文泉驿 | 候选只有 macOS/Windows/上游路径 | 加镜像已有的 AR PL SungtiL GB、AR PL KaitiM GB（TeX Live 带入，TrueType 可嵌入）和 Liberation Serif |
| HTML 路线写 Google Fonts `<link>`、Inter/Noto Sans SC/Material Icons/emoji 名字在镜像里解析到 DejaVu | 断网沙箱没有在线字体 | 删 Google Fonts 链接与死代码；镜像加 Inter、Material Icons、Noto Color Emoji 并在 fontconfig 加别名；海报的 Material Icons 用本地 `@font-face` |
| fontconfig 别名对 Chromium 无效 | Skia 只接受 `binding="same"` 的替代 | 全部别名改 `binding="same"` |
| `gs`、`qpdf` 命令、DejaVu、FreeSerif、Liberation、Carlito/Caladea 未安装 | `--no-install-recommends` 下不会被带入 | 加入 apt 清单 |
| 学术路线说 Tectonic 会联网下载 | 镜像是离线 XeLaTeX | 文案、`env_check.sh`、`env.check` 标签改为 XeLaTeX |
| docx `ooxml.md` 写 `/mnt/skills`；示例图片用远程 URL | 上游路径 | 改 `$DOCX_SKILL_DIR`（镜像新增 4 个 `*_SKILL_DIR` 环境变量）；远程图先 `download_url` 再用本地路径 |
| 生成的文件写入 `Z.ai`/`Zhipu AI` 作者、批注缩写、`meta.brand` | 上游品牌 | creator 写 `AILab`，作者只在用户已知时填；`meta.brand` 只设 Title。资源 frontmatter 的 `author: Z.AI` 是真实上游署名，保留（不进用户文件） |

所有技能文本改动都在 `vendor/office-suite/patches/013`、`014` 里（生成物从补丁重建，可复现）；新测试 `test_generated_resources_only_promise_what_the_image_has` 拦住这些上游假设回流。

## 检查过、没问题

- 系统提示词与技能导航的工具名一致；技能文本里没有让模型派 subagent/并行代理的话；子任务仍不能 `load_skill`/`review_artifact`/`deliver_file`。
- 前缀缓存：`load_skill` 结果只追加；本轮只有 Work 提示词与 `run_subtasks` 描述变了，走既有的 `platform_rules_changed` 边界，每个 Work 会话部署后重建一次。
- 镜像内（`--network none`）技能文本代码块里引用的每个绝对路径、Python import、命令逐个核对：剩余未命中的只有多平台候选列表（代码先判断存在再用）。

## 验证（基线：分支工作树，`main@99602a5a` 之上）

- `ruff check` / `ruff format --check` 通过。
- 技能/办公/前缀缓存/分叉/子任务相关 23 个测试文件 387 passed；新增跨部署读取用例去掉修复即失败。
- 本机 arm64 完整构建 `sbx-office`（4.98 GB），`AILAB_TEST_OFFICE_IMAGE` 契约测试 5 passed（含新加的 `convert.html`/`convert.blueprint`/封面字体断言，改前失败）。
- 镜像内实测：三份 `env_check.sh` 与 `pdf.py env.check` 全 ✓；report.md 字体块渲染并嵌入 WenQuanYi/Liberation/Carlito/DejaVu；matplotlib 中文无缺字警告；Chromium 嵌入 Inter、Noto Sans/Serif CJK、Material Icons（渲染为图标）、Noto Color Emoji；LibreOffice 把 Calibri/Cambria/Times/Arial/微软雅黑 映射到 Carlito/Caladea/Liberation/Noto；`gs`、`qpdf`、XeLaTeX 中文、`convert.latex` 通过。

未测：完整套件与 E2E（交给 CI）、amd64 镜像。真模型与跨部署见第三轮。

## 成本与部署

- 镜像新增包安装体积约 64 MB（Ghostscript 22 MB、FreeFont 14 MB、Noto Color Emoji 11 MB，其余各 ≤ 4 MB），约占 4.98 GB 镜像的 1.3%；运行时无常驻开销。
- 部署须同时更新 Agent 与 sbx-office 镜像（`index.json` 变了）；Work 会话在下一轮重建一次前缀。

## 第三轮：选中段落、用户消息构造与真模型场景（2026-11-13）

### 选中段落（doc_refs）查出的问题

选中段落本身只追加在用户消息末尾，不改前缀；但同一条用户消息在三处各自拼，拼法不一致，续轮按历史重建时与模型当初读到的不是同一段文字，前缀从这条消息起失效：

| 问题 | 根因 | 修复 |
|---|---|---|
| 技能标记在实时消息里插在原文偏移处，历史重建时插在「定时任务头 + 原文」上，偏移错位 | 实时路径先包定时头再插技能，历史路径反过来 | `prompts.user_text` 成为唯一拼法：先按原文偏移插技能，再包定时头，最后追加选中段落块；实时、追加（steer）、两条历史接口都调它 |
| 历史只认 `options.skills`，旧格式 `skill`/`skill_title` 的轮次重建时丢了技能标记 | 历史项另存了一份 `skills` | `turn_skills(options)` 统一兼容新旧两种格式；历史项不再单列 `skills` |
| 定时任务那一轮里用户追加的消息（steer）也被包上定时任务头 | steer 继承了 run 的全部 options | steer 去掉 `schedule` |

两个新用例（`test_prefix_cache.py` 的 steer/定时/技能/选中段落同构，`test_document_view.py` 的两条历史接口逐字等于实时文本）去掉修复都失败。

### 固化的真模型场景

`platform/scenarios/`：经公开 API（与 Portal 同路）对任意运行中的栈跑代表任务，默认跳过，`AILAB_SCENARIOS=1` 才跑（计费）；每个场景结束删除自己建的会话、上传文件和交付件。每轮都检查：run 成功、会话内无前缀边界、未要求就不派子任务、续轮 `cached/prompt ≥ 0.85`（可调）。场景：Chat 三轮推算、Chat 选中库文件段落提问、Chat 附件追问、Work 从零写 Word、改 Excel 保留公式、做 PPT、中文 PDF 嵌字体、Work 选中交付件段落让它改、并行任务默认单 Agent / 要求时才派子任务。用法见 `scenarios/conftest.py` 与 AGENTS.md §9。

### 真模型结果（deepseek-flash，本机栈）

| | 基线 `adb0ecf` | 本分支 `055a0b6` |
|---|---|---|
| 通过 | 8/8 | 9/9（新增附件追问） |
| 续轮缓存命中（全部续轮合计） | 94.4% | 94.1% |
| Chat 三轮：第 2/3 轮 | 97.3% / 96.0% | 98.4% / 98.7% |
| Chat 选中段落第 2 轮 | 96.4% | 95.0% |
| Work 改选中交付件段落第 2 轮 | 97.9% | 97.0% |
| Chat 附件追问第 2 轮 | — | 99.1% |
| 子任务 | 未要求不派、要求时派 | 同左 |
| 中文 PDF | 21 次调用 / 819k prompt tokens（一次复跑绕 docx→soffice） | 17 次 / 615k，直接 ReportLab，不再折腾字体 |
| PPT | 39 次 / 1.34M，缓存 95.9% | 38 次 / 1.86M，缓存 97.3% |

失败的工具调用都是模型自己的错并自行恢复（页码越界、脚本 exit 1）。单次采样方差大（Word 14 → 20 次调用、Excel 9 → 6 次），表中数字只看量级。

跨部署（旧镜像开会话，换本分支镜像后续轮）：Chat 续轮 99.1%、无边界；Work 续轮出现一次预期的 `rebuilt` 边界（Work 提示词版本变更），`load_skill` 读新包成功，下一轮 98.4%。

### 未覆盖 / 风险

- 工作区文件（`run_id` + `path`）形式的选中段落没有场景覆盖，只有单元测试。
- 真模型只测了 deepseek-flash；PPT 任务成本高（会用 web_search/search_images），属任务性质，未改。

## CI 与合并

- [PR #191](https://github.com/shijunan126-creator/ailab-lite/pull/191) 首轮 SQLite 挂 1 项：`test_conversations` 仍期望内部历史项带 `skills` 键；本轮有意去掉（技能标记已由 `user_text` 写进 prompt 文本），改了期望。
- `c96b3775` 15 项全绿：SQLite 3053 passed / 225 skipped，PostgreSQL 3187 passed / 91 skipped，E2E 三片 196 passed / 15 skipped。
- squash 合并为 `09519b52`，提交树与 `c96b3775` 一致。
