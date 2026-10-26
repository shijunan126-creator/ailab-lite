# 参考实现分析：记忆读取与治理

按架构文档 §37.4 记录。读取时间 2026-10-26，源码在本机 `/root/src-readonly/{mem0,opendream}`（浅克隆，只读，未构建未运行）。

```yaml
subsystem: 历史对话检索 + 写入闸门 + 删除沿溯源
references:
  - project: Mem0 (Apache-2.0)
    files_or_modules:
      - mem0/utils/scoring.py            # BM25 sigmoid 归一化、查询长度自适应参数、semantic+bm25+entity 加性打分
      - mem0/memory/                      # 抽取 / 检索入口（ADD-only 路径）
  - project: OpenDream (Apache-2.0)
    files_or_modules:
      - opendream/retriever.py            # should_retrieve 闸门、词法重叠率、excluded 记录与 reason_code、rerank 歧义阈值
      - opendream/reconciliation.py       # 孤儿 / 失效引用的有界修复，不静默销毁溯源
      - opendream/verifier.py             # 写入前验证与 quarantine/contested 操作

reused_concepts:
  - OpenDream：检索前先判断是否需要检索，并允许无结果（这里由模型调用工具承担 gate，工具返回 NO_RELEVANT_MEMORY）
  - OpenDream：以“问题词被覆盖的比例”作词法相关度（这里改成 IDF 加权，并加标识符直通）
  - Mem0：BM25 归一化到 [0,1] 后与其他信号相加（这里没有稠密信号，改用覆盖度做绝对阈值）
  - OpenDream：清理/修复不静默销毁溯源 → 删除对话时清引文与证据 id，但保留事实并标记 evidence_deleted

reused_code: []   # 没有复制代码，所以无需署名；dream 的两段 prompt 署名见 PR #80

deliberate_differences:
  - Mem0 在 OSS 路径里 BM25 只作为语义候选之上的加分；这里没有语义候选，词法通道独立产生候选（与架构文档 §10.3 一致）
  - Mem0 的 BM25 参数针对词形还原后的英文；这里中文是二元组、英文是原词，所以不用它的 sigmoid 常数，改用语料内 IDF
  - 小语料下不存在词的 IDF 取“出现一次”的权重，否则新用户的少量历史会被一个未出现的词整体压过阈值（测试 recall_never_crosses_users 里复现过）
  - 检索直接读 runs，不另存 episode 副本：删除、编辑、分叉都立即反映

reasons:
  - 本部署没有 embedding 模型和向量库，也不应为一个 100 条上限的记忆引入它们
  - 不另存历史副本，就没有第二份需要随删除同步的数据

risks:
  - 纯词法：同义改写（“容器编排” vs “Kubernetes”）召回不到。靠用户提问时通常复述主题词缓解；需要时接入稠密通道
  - 中文二元组对短查询噪声较大；覆盖度阈值（0.4）是经验值，尚未用真实对话调参
  - 只扫最近 400 个已成功轮次；更早的历史查不到（可用 days 以外的方式扩大，但要加索引）
```
