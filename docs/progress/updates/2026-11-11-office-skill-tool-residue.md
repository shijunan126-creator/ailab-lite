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

- 完整工具清单、v1/v2 覆盖关系、未修残留 R1–R6、与 Pi 四工具的对照都记在[工具面与残留清单](../../reviews/2026-11-11-office-skill-tool-inventory.md)；本节不重复。
- visual-judge 那句是判断、不是事实修正，而且写错了：`run_subtasks` 是 2–4 个子任务，一句「交给一个子任务」不成立；平台默认的视觉门禁是常驻的 `review_artifact`，不是子任务。待确认后改成「先自己用 `review_artifact`/`view_workspace_file` 逐页判定，长成品再拆 2–4 个子任务」。
- 生成的 `images/sbx-office/skills/index.json` 变了，而 Agent 会用它和租约沙箱的索引做一致性校验（`skill_verification.py`）：先上 Agent 代码再重建 sbx-office 镜像的话，`load_skill` 会以“技能清单与沙箱镜像不一致”被拒。部署时必须同时重建镜像。
- 未跑完整套件、Portal、E2E 和真模型；本轮只改技能正文文本，未改请求构造。
