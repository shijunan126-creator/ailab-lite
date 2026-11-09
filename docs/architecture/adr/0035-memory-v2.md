# ADR 0035 · 每个作用域一份有界记忆文档

状态：方案已决定，实现与验收尚未完成（2026-11-09）。替换此前核心/目录、检索、独立 judge 与 consolidation 方案。

`memory_items` 是唯一内容来源；账号与 isolated 项目分别组成完整文档，按 fact/preference/project/interest/reference 分节。默认渲染上限 6000 字，用户明确添加允许到 9000 字，超过返回 422。旧数据不得静默截断；下一次后台整理处理超限。保留版本关闭、来源授权、墓碑、敏感 review、租约、计费和可撤销 delta。

系统消息一包含共用静态规则，消息二只包含数据。消息二是**会话快照**，每个新 run 重用原消息；仅 compaction/model/access/ABI/rebuild 边界重建。外部修改末尾追加一条增量，当前 run 的工具写入不重复播报。禁止 TTL 重绘。工具 ABI 升为 3：memory 仅 add/replace/remove；关键词查询原始历史移到 recall_history。

每批 Dreaming 一个调用：静态规则、新 episodes、完整当前文档（最后）。输出 add/update/merge/delete JSON ops；同一调用在预测使用量超过85%时压至75%。程序校验引文、来源、id、墓碑和受保护条目，最后才淘汰最老的未强化 inferred 行。显式/edited 行不能被整理删除或合并，更新必须有较新用户引文。

0065 清理 artifact 及 undo 副本，不写墓碑。0066 删除 entity/alias 投影，并让活动唯一键包含 kind。当前实现暂留 memory_items 的旧列；是否能安全直接 DROP COLUMN 尚待复核，不能把保留旧列称为清理完毕。

参考：[OpenAI Dreaming](https://openai.com/index/chatgpt-memory-dreaming/)、[Claude memory 与历史搜索](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)、[Mastra Observational Memory](https://mastra.ai/research/observational-memory)。借鉴后台综合、稳定上下文和原始历史按需查询，不声称这些产品采用同一存储实现。Mastra 报告的94.87%是其特定模型/数据集结果，不是本项目质量证明；其 Observer/Reflector 也不是本方案的单调用实现。

本轮实测、限制与剩余工作只维护在[实施记录](../../progress/updates/2026-11-09-memory-document.md)。
