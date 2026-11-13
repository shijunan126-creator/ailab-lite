# 思考块按标记展开/收起（2026-11-13）

分支 `fix/process-collapse-on-answer`，在 `main@ef76d55a` 上开发并验证，提交前 rebase 到 `main@910f1525`（新增的提交只改了预览恢复，与 Portal 无交集）。

## 目标与取舍

用户要求：思考过程中展开，正式回答一开始就收起，界面不乱跳。

本轮先实现了「回答文本一出现就折叠整个过程框」，复查时否决：Chat 和 Work 都带工具，最后一步之后出现的文字在流式时无法区分是正式回答还是调工具前的旁白，模型写第一个字时自己也没决定。按这个猜测折叠，在每步都先说一句再调工具的运行里会反复折叠、展开。

最终采用能准确判断的信号：思考与正文是不同通道（`agent.thinking` / `agent.delta`），模型在思考之后做的任何事都会把思考标记为 `done`。

- **思考块**：没有手动选择时，展开状态 = 正在写（`live && !done`），写完立刻收起，这和 ZCode 等产品的思考块一致。读者的选择按模型步骤记在 `Turn` 里（`Map<step, boolean>`），优先于自动状态。写完的思考仍只显示标题，点开才挂载全文（沿用 2026-10-26 的规则）。
- **过程框**：沿用 main 的行为，工作期间展开，结束后收起。不再按「回答已开始」折叠，也不再把标题改成「正在回答」。
- **平滑**：时间线有最大高度（340px），对思考块本身做高度动画时，外框只在动画最后几十毫秒才收缩，仍会一下跳过去。改为 `useSmoothShrink`：内容变矮时，时间线从原来显示的高度过渡到新高度，在缩小内容的同一次 commit（`useLayoutEffect`）里就启动；ResizeObserver 兜住不经过这次 commit 的收缩。内容变高不做动画。过程框整体收起时，仍保留到 `transitionend` 才卸载正文（`useCollapse`），超时兜底时间从计算出的 `transition-duration` 推得。

## 验证（当前工作树）

- Portal：`npm test` **295 passed**；`npm run build` 通过；`tsc` 除 `.next` 里的旧类型产物外没有报错。
- 新单测 `tests/work-process-collapse.test.tsx`：Chat 和 Work 的思考都是边写边展开，正文一来就收起；有工具调用时思考收起、下一段思考展开，过程框始终展开；手动选择优先；运行结束后全部收起。
- 浏览器 E2E（生产 Portal + 脚本化 Agent，临时 SQLite + Redis 16379）：`test_work_process.py` 全部通过，`test_full_reasoning_and_early_title_while_answer_streams` 通过。新的逐帧用例 `test_thought_folds_when_the_answer_starts_without_a_frame_jump`：长思考收起时过程框从 306px 降到 92px，中间经过多个中间高度，过程框高度和回答位置在任何一帧的变化都不超过总变化的 30%。
- `test_full_400k_thought_from_process_is_readable_with_bounded_dom`（Chat/Work × 桌面/手机，共 4 条）和 `test_portal.py` 里与思考、过程、Chat 有关的 4 条用例，都在最终代码上通过。

## 边界

- 没有 Python 产品代码改动，所以没跑完整 Python 套件，交给 CI。
- 未部署。
- 只在 Playwright 桌面视口测了逐帧数据，没有在真机上看动效。
- 思考通道的来源兼容（vLLM 不开 reasoning parser 时 `<think>` 会混在正文里，以及 Higress 透传问题）正在另行调研，本次没改；思考如果混在正文里到达，这套「按标记收起」就不会生效。
