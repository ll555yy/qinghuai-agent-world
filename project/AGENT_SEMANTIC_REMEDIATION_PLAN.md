# Agent 语义评测问题修复方案

- 状态：实现、离线/PostgreSQL 门禁和经授权真实复评已完成；剩余 Judge/人工限制已记录
- 基线日期：2026-08-23
- Candidate：`doubao-seed-2.0-lite`
- 当前基线：47 Case、81 个规则 observation、30 个 hard failure、23 个 Case 直接通过、24 个 Case 需要复核
- 原则：先判定问题归属，再做最小修复；安全硬门不能被 Judge 高分覆盖；不通过删 Case、放宽约束或隐藏失败改善报表

## 1. 阶段目标

本阶段不新增玩法、模型或 Agent 框架。目标是使用现有 47 Case 真实基线完成一次可审计的质量修复闭环：

1. 区分 Candidate 真缺陷、评测输入投影缺失、RuleScorer 误判、Case 约束歧义和 Judge 误判；
2. 修复 ID/owner/evidence scope、时间边界、离场角色、直接提问等确定性问题；
3. 在确定性硬门稳定后，针对 Goal 推进、相关性和玩家自主性做单变量 Prompt 实验；
4. 把协议原始质量、后端最终拦截能力、数据库检索质量和 Judge 可信度拆成四套指标；
5. 用相同版本化 Case 形成 before/after 报告，不覆盖 2026-08-23 基线。

## 2. 不可违反的边界

- 不删除原有失败 Case，不修改历史基线报告。
- Case 约束只能因事实错误、字段投影缺失或语义歧义修订；每次修订必须提升 `caseVersion` 并记录理由。
- 不把后端已经拦截的模型原始违规描述成真实世界状态泄漏，也不能因后端拦截而忽略模型质量问题。
- 不把 query hint 中的 Actor/Goal/Topic ID 自动等同于已经读取了未授权 Memory；只有越权返回、越权证据或明确跨 owner 内容才算系统级 owner 失败。
- 不用 Judge 分数覆盖 Schema、ID、owner、evidence、time、departed、participant 或 world mutation 硬失败。
- 不在普通自动化测试中访问真实方舟或开发数据库。
- 不换 Candidate 模型，不增加多模型复核，不修改 Day1-Day7 剧情、NPC 人设、Goal 和固定结局规则。
- 未得到用户明确授权前，不运行产生真实网络请求或费用的复评。

## 3. 问题分层

### 3.1 协议原始质量

衡量模型通过 `DecisionService` 输出的协议对象是否符合当前 Case 的合法动作、候选 ID、证据和时间要求。该层保留 raw Candidate violation，用于 Prompt/Schema 调优。

### 3.2 系统最终安全

衡量 `RunService`、Pydantic 协议和 owner-safe 工具是否阻止非法结果进入权威状态。必须单独记录：

- `candidateViolation`：模型原始决定不合法；
- `systemBlocked`：后端成功拒绝或安全回退；
- `endToEndSafetyFailure`：非法内容真正进入权威状态或公开投影。

模型违规但被系统阻止，不能算端到端安全失败；同时仍算 Candidate 质量缺陷。

### 3.3 检索质量

fixture 检索和 PostgreSQL/pgvector 检索分别报告。`Precision@K`、`Recall@K`、MRR、空召回、vector/graph hit 和 owner 隔离不得混用假数据与真实数据库结果。

### 3.4 Judge 可信度

Judge 只提供语义信号，不裁决确定性硬门。不同协议使用不同 Rubric；结构化决策不得仅因为是 JSON 就被判定为“不自然”。

## 4. 实施阶段

### 阶段 A：冻结基线并建立诊断矩阵

1. 保留当前 `live-baseline-2026-08-23`，生成稳定 SHA-256 和基线摘要。
2. 对 15 个重复出现 hard failure 的 Case 逐 observation 建表，至少包含：
   - Case、协议、失败码、Candidate 输出摘要；
   - Case 允许的 actor/goal/evidence/memory scope；
   - 生产 Pydantic Schema 是否有效；
   - RuleScorer 判定路径；
   - 问题归属：`candidate | projection | scorer | case | judge | mixed`；
   - 是否可能进入权威状态、当前后端是否已拦截。
3. 将报告中的失败输出转为脱敏、离线可重放的 regression fixtures；复现不得调用网络。
4. 把 `protocolSchemaValid` 与 `caseConstraintValid` 拆开，避免“Pydantic 合法但违反 Case”统一标成 `schema_invalid`。

阶段 A 完成条件：30 个 hard failure observation 全部具有唯一、可解释的问题归属；任何无法归属项进入人工复核，不能直接改 Prompt。

### 阶段 B：修复评测投影和规则误判

优先审查当前集中失败的几个位置：

1. `ChatDecision.memoryQuery` 内的 `actorIds/goalIds/topicHints` 是否被 observation builder 正确提取；不得把合法查询 hint 当成已召回 Memory ID。
2. Case 是否完整注入合法 actor、goal、agenda、evidence、memory owner 范围。
3. `RuleScorer` 是否分别判断 query scope、retrieval result scope 和 committed evidence scope。
4. `InvitationDecision` 没有 `action` 字段时，不得按 ChatDecision 的动作规则误判。
5. `DailyActionDecision(action=wait)` 不得要求 `goalId/targetActorId`；`seek_chat` 才要求两者合法。
6. Judge 对结构化协议只评适用维度，不对 JSON 载体本身扣自然度。

所有修复必须带最小单元测试，并证明不会降低 owner、canary、internal field、evidence 和 world mutation 安全门。

### 阶段 C：修复真实 Candidate 确定性缺陷

按下列顺序逐批修复，每批只改变一个协议或一条规则：

1. `DailyActionDecision`：候选 actor/goal、17:00 cutoff、`wait` 形状。
2. `InvitationDecision`：departed、满员、非法参与条件必须拒绝；玩家决定仍不能由 Agent 代替。
3. `ChatDecision`：memory/evidence/agenda scope、禁止其他 NPC Goal、禁止世界状态写入。
4. 直接提问：仍在会话中的玩家明确向当前 NPC 提问时必须作出可见回答，但 NPC 可拒绝、反对或提出条件。
5. 召回限制：同一触发消息最多一次，空结果和第二次请求安全结束。

优先使用后端确定性校验保证世界安全；只有需要角色语义选择的部分才调整 Prompt。不得把时间、参与者上限、departed 或权威状态合法性只交给模型保证。

### 阶段 D：语义质量最小实验

确定性问题关闭后，再针对以下问题建立小规模实验：

- 直接回答；
- Goal 推进；
- 回应相关性；
- 玩家自主性；
- 连续复读。

每项遵循：固定 Case 子集 -> 保存旧输出 -> 修改一个 Prompt 规则 -> offline 回归 -> 经用户授权后 live 两次 -> 对比成功率、P95、Token 和费用。若改进一个维度却使安全硬门或其他核心维度显著回退，立即回滚该实验。

### 阶段 E：Judge 协议化与校准

1. 为六类协议建立适用维度：
   - `SpeechGeneration`：persona、faithfulness、relevance、naturalness、goal progress、player agency；
   - `ChatDecision`：faithfulness、relevance、goal progress、player agency、证据一致性；
   - `DailyActionDecision` / `InvitationDecision`：以规则为主，只评语义理由与上下文一致性；
   - `SegmentSummary`：事实忠实、遗漏、编造；
   - `ExitConsolidation`：证据、归属、Goal/关系/章节变化一致性。
2. 复核 13 个校准 Case 的 expected 标签；只能修正有人工证据的歧义，不得为了贴合 Judge 输出改标签。
3. 增加至少两名人工标注者或两轮独立人工标注，记录分歧和最终仲裁。
4. 校准报告增加 critical boolean、major issue 和 score band 的分项混淆矩阵。
5. Judge 未达到校准门槛时继续只作辅助信号，不驱动自动发布或 Prompt 自动优化。

### 阶段 F：PostgreSQL 检索闭环

在专用 `QINGHUAI_TEST_DATABASE_URL` 上运行并固定：

1. `DatabaseMemoryRetriever -> RuleScorer` 集成；
2. run + owner 过滤；
3. vector、Actor、Goal、Topic、关键词与 1-2 hop Graph；
4. 相似的其他 owner Memory 不得进入候选、扩展和最终结果；
5. 真实数据库 Precision@K、Recall@K、MRR 和空召回率单独报告。

### 阶段 G：最终复评与交付

1. 先运行全量离线测试、Ruff、mypy、应用导入和密钥扫描。
2. 生成 dry-run 预算；没有用户明确授权时在此停止并报告预计调用与费用。
3. 获得授权后，用相同 Case 运行新的 live baseline，不覆盖旧基线。
4. 生成 before/after 表、剩余 Bad Case、人工仲裁队列、Judge 校准和 PostgreSQL 检索报告。
5. 只保留 canonical baseline、offline fixture 和必要验收文档；probe 中间报告不得进入最终交付。

## 5. 验收门槛

离线与系统安全门必须全部满足：

1. 原 47 Case 全部可加载，历史 Case 未静默删除；修订 Case 均提升版本并记录原因。
2. owner/canary/internal literal leak 为 0。
3. `endToEndSafetyFailure=0`：非法 ID、evidence、time、departed、participant 和 world mutation 均无法进入权威状态。
4. 同一触发消息 Memory 工具单次调用通过率 100%。
5. 15 个原 hard-failure Case 均有离线 regression test 和明确归属。
6. PostgreSQL owner-safe 集成通过，不再因缺测试库跳过本阶段关键检索证据。
7. pytest、Ruff、mypy、前端既有门禁继续通过。

真实复评目标（必须经用户授权后验证）：

- 首轮协议 Schema 成功率 >= 90%，最终协议 Schema 成功率 >= 95%；
- 安全/权威类 hard failure 为 0；
- 直接提问规则通过率 = 100%；
- PostgreSQL Memory Precision@K >= 0.75、Recall@K >= 0.90、owner 越界 = 0；
- Judge 校准通过率 >= 80%，3/3 Injection 通过；未达到时必须标记 Judge 为 advisory；
- 不要求为了达标伪造分数；未达到的指标如实保留为后续 Bad Case。

## 6. 交付物

- `project/AGENT_SEMANTIC_REMEDIATION_DESIGN.md`
- 问题归属矩阵和脱敏 regression fixtures
- Candidate/Projection/Scorer/Case/Judge 的修复代码与测试
- 协议级 Judge Rubric v2 与校准报告
- PostgreSQL 检索集成报告
- 新旧 baseline before/after 报告
- `project/AGENT_SEMANTIC_REMEDIATION_ACCEPTANCE.md`
- 一个清晰 Git 提交；不得混入无关视觉、前端、七日模拟或 probe 报告
