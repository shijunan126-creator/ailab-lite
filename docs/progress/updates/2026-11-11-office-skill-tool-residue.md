# 办公技能工具名残留清理（2026-11-11）

基线 `origin/main@bc5c5b5c`，分支 `fix/office-skill-residue`（独立工作树 `.worktrees/office-skill-residue`，主工作树不动）。未推送、未开 PR、未部署。

## 背景

回答"ZCode 办公 skills 调用哪些工具、提示词是否写死 ZCode 工具"时逐文件核对：生成物里没有 ZCode 工具名（`tools: [Read, Bash]`、`open_document`/`show_in_viewer`、`::zcode-file-citation`、`AskUserQuestion`、`ZCODE_*` 都已在补丁 001–008 改写），但有三处仍在指代不存在的东西。本轮只修这三处，不改工具目录，不实施"换成 Pi 的 read/bash/edit/write"。

## 改动

新增补丁 [`013-tool-name-residue.json`](../../../vendor/office-suite/patches/013-tool-name-residue.json)（登记在 `series.json`），改的是四份 `agents/visual-judge.md`、pptx `resources/SKILL.md`、pdf `briefs/creative.md`：

| 位置 | 原文 | 改为 |
|---|---|---|
| 4× visual-judge 描述 | `pick one gate — spawn visual-judge, or …` | 交给 `run_subtasks` 子任务，或自己按同一标准判定 |
| 4× visual-judge 描述尾 | 视觉门禁 `planned:P2`，来源交付协议"未提供" | 只读 `review_artifact`/`view_workspace_file` 返回的页面，由产出该文件的 run 修复 |
| 4× visual-judge 正文 | `## What you will receive (in the dispatch message)` | `… (in the subtask instruction)` |
| pptx `resources/SKILL.md` | `use the visual-judge subagent (if not present, check it yourself)` | `review_artifact`/`view_workspace_file` 逐页判定，可把页面和 `agents/visual-judge.md` 交给 `run_subtasks` |
| pdf `briefs/creative.md` 0a | `did I ask the user via user clarification instead of picking a fallback` | `did I report the missing dependency and stop this route …` |

最后一条同时修掉与文件开头"依赖预装、缺失即停"的矛盾：原文让模型去问用户装 Playwright，与离线契约相反。

## 验证

```bash
cd platform
python -m pytest tests/test_office_suite_resources.py tests/test_office_skill_checks.py \
  tests/test_platform_skills.py tests/test_document_catalog_v2.py \
  tests/test_skill_modules.py tests/test_base_office_skills.py -p no:warnings
# 106 passed in 19.19s
```

- `vendor/office-suite/build.py` 重建后与提交的生成物逐字节一致（`test_rebuild_reproduces_every_generated_byte` 覆盖），`modules.json`/`index.json` 的哈希随之更新。
- `platform/ailab_platform/agent/office_guides_v2.json` 未变：V2 指南的 viewer 覆盖只依赖 `viewer/SKILL.md`，本轮没动它。
- 生成物中 `spawn`/`subagent`/`dispatch message`/`user clarification`/`planned:` 全部 0 命中。
- 工具目录、system prompt 都没变，`load_skill` 的内容是追加的工具结果，不需要新的前缀缓存边界记录。

## 未覆盖与风险

- 同一来源的另一类残留没动：`platform.*/env_setup/setup.md`（4 份，共 835 行）和 pptx `resources/SKILL.md` 的 `## Dependencies` 仍留着国内镜像地址与 `sudo dpkg`/`hdiutil`/`msiexec` 安装命令，与"依赖预装"契约冲突；`setup*.sh` 已被补丁改成 `exit 1`，但 `.md` 里的命令模型仍能读到。要不要清、清到什么程度待定。
- visual-judge 的 frontmatter 仍保留上游的 `color: yellow` 和 `tools: [view_workspace_file]`；AILab 不解析资源文件的 frontmatter，没有功能影响，本轮未动。
- 生成的 `images/sbx-office/skills/index.json` 变了，而 Agent 会用它和租约沙箱的索引做一致性校验（`skill_verification.py`）：先上 Agent 代码再重建 sbx-office 镜像的话，`load_skill` 会以"技能清单与沙箱镜像不一致"被拒。部署时必须同时重建镜像。
- 未跑完整套件、Portal、E2E 和真模型；本轮只改技能正文文本，未改请求构造。
