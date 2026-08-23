# 下一阶段 Goal：修复 Agent 语义评测暴露的问题

以下正文供用户复制后自行设置 Goal；本文件不代表已经创建或启动 Goal。

```text
继续开发 C:\Users\yangruiqi\Desktop\chat 中的“青槐老巷聊天世界”。

目标：基于现有 47 Case Agent 语义评测和 2026-08-23 真实基线，完成一次可审计的 Candidate 质量修复闭环。先区分 Candidate 真缺陷、评测输入投影缺失、RuleScorer 误判、Case 歧义和 Judge 误判，再修复确定性硬问题、直接提问与语义质量；用相同版本化 Case 形成 before/after 证据。不得通过删除失败 Case、放宽安全约束、隐藏 Bad Case、直接换模型或让 Judge 高分覆盖硬失败来改善结果。

开始前完整阅读并遵守：
- project/PROJECT_RULES.md
- project/PROJECT_DESIGN.md
- project/SYSTEM_DESIGN.md
- project/AGENT_SEMANTIC_EVALUATION_DESIGN.md
- project/AGENT_SEMANTIC_EVALUATION_ACCEPTANCE.md
- project/AGENT_SEMANTIC_EVALUATION_BASELINE.md
- project/AGENT_SEMANTIC_REMEDIATION_PLAN.md
- core/evaluation/agent_semantic_cases.yaml
- core/evaluation/judge_calibration_cases.yaml
- core/backend/app/evaluation/ 下的全部实现

先检查 Git 工作树并保护所有用户已有修改。当前工作区包含尚未提交的七日可达性、语义评测代码和报告；不得覆盖、回滚、删除或顺手格式化不属于本 Goal 的变更。先把本阶段设计和验收标准写入 project/，再修改代码。

已知真实基线：
- 47 个 Case、81 个规则 observation；
- 30 个 hard failure，hard failure rate 37.037%；
- 23 个 Case 直接通过，24 个 Case 进入 Bad Case/人工队列；
- Schema success 65.4321%，first-attempt 70.2128%；
- direct-question rule pass 0%；
- Memory Precision@K 47.4359%，Recall@K 100%，MRR 92.3077%；
- owner/canary/internal literal leak 均为 0，但 22 个 observation 被标记为 unauthorized memory / owner-boundary violation；
- Judge 六维均值中 goal progress 2.7073、player agency 2.9024；
- Judge 校准只通过 2/13，Injection 通过 2/3；当前 Judge 只能作为辅助信号。

必须实施：

1. 冻结和复现基线
- 不覆盖 `project/evaluation-results/live-baseline-2026-08-23`。
- 为基线生成稳定摘要和 SHA-256。
- 将 15 个重复 hard-failure Case 的两次 observation 转为脱敏离线 regression fixtures；普通测试不得访问网络。
- 建立问题归属矩阵，逐项标记 `candidate | projection | scorer | case | judge | mixed`，并记录是否可能进入权威状态、现有后端是否已拦截。
- 30 个 hard failure observation 在修改 Prompt 前必须全部有明确归属；无法判定的进入人工复核。

2. 拆开四类指标
- 协议原始质量：模型通过 DecisionService 产生的动作、ID、evidence 和结构化输出是否合法。
- 系统最终安全：RunService/Pydantic/owner-safe Tool 是否阻止非法结果进入权威状态。
- PostgreSQL 检索质量：真实 DatabaseMemoryRetriever 的 Precision@K、Recall@K、MRR、vector/graph hit 和 owner 隔离。
- Judge 可信度：协议级 Rubric、校准和稳定性。
- 在报告中明确区分 `candidateViolation`、`systemBlocked` 和 `endToEndSafetyFailure`。模型违规但被系统安全拦截时仍算 Candidate 缺陷，但不能冒充真实世界状态泄漏。
- 将 `protocolSchemaValid` 与 `caseConstraintValid` 分开；Pydantic 合法但违反 Case 不能统一写成 `schema_invalid`。

3. 审计评测投影与 RuleScorer
- 检查 ChatDecision.memoryQuery 内 actorIds/goalIds/topicHints 的提取和授权范围。合法 query hint 不等于已经读取未授权 Memory；只有越权返回、越权 evidence 或明确跨 owner 内容才算系统级 owner 失败。
- Case 必须完整提供合法 actor、goal、agenda、evidence、memory 和 owner scope；若修订 Case，提升 caseVersion 并记录事实依据。
- RuleScorer 分开判断 query scope、retrieval result scope 和 committed evidence scope。
- InvitationDecision 不得按 ChatDecision 的 action 字段规则误判。
- DailyActionDecision(action=wait) 不要求 goalId/targetActorId；只有 seek_chat 才要求合法目标。
- 修复必须有最小单元测试，不能降低 owner、canary、internal field、evidence 和 world mutation 硬门。

4. 修复 Candidate 确定性问题
- DailyActionDecision：候选 actor/goal、wait 形状和 17:00 cutoff。
- InvitationDecision：departed、满员和非法参与条件必须 refuse；玩家选择仍不能由 Agent 代替。
- ChatDecision：memory/evidence/agenda scope、禁止其他 NPC Goal、禁止世界状态写入。
- 直接提问：仍在会话内的玩家明确向当前 NPC 提问时，NPC 必须给出可见回答，但可以依据人设拒绝、反对或提出条件。
- Memory：同一 NPC/触发消息最多召回一次；空结果、工具异常和第二次请求安全结束。
- 权威规则优先由后端确定性校验保证，不能只依赖 Prompt；角色语义选择才交给模型。
- 每批只修改一个协议或一条规则，运行对应 Case 和全量离线回归后再进入下一批。

5. 进行语义质量单变量实验
- 只在确定性硬问题关闭后处理直接回答、Goal 推进、相关性、玩家自主性和复读。
- 每项固定 Case 子集、保存旧输出、只改一个 Prompt 变量、先 offline，再在用户授权后 live 两次。
- 记录成功率、P95、Token、估算费用和其他核心维度回退。安全硬门或其他核心维度显著回退时回滚。
- 不修改 NPC 人设、Goal、剧情或结局阈值来迎合 Case。

6. 将 Judge 改为协议级 Rubric v2
- SpeechGeneration 评 persona、faithfulness、relevance、naturalness、goal progress、player agency。
- ChatDecision 评 faithfulness、relevance、goal progress、player agency 和证据一致性；不要因 JSON 载体扣自然度。
- DailyActionDecision/InvitationDecision 以确定性规则为主，只评语义理由和上下文一致性。
- SegmentSummary 评事实忠实、遗漏和编造。
- ExitConsolidation 评 evidence、owner、Goal/关系/章节变化的一致性。
- 复核 13 个校准 Case 的 expected 标签，只修正经人工证据确认的歧义，不得为了贴合 Judge 输出改标签。
- 至少进行两轮独立人工标注，保存分歧和仲裁；报告增加 critical booleans、major issues 和 score band 的分项结果。
- Judge 校准未达到 80% 或 3/3 Injection 未通过时，明确标记 advisory，不用于自动发布或自动调 Prompt。

7. 完成 PostgreSQL 检索证据
- 使用专用 QINGHUAI_TEST_DATABASE_URL 运行 DatabaseMemoryRetriever -> RuleScorer 集成测试；不得连接开发库或生产库。
- 覆盖 run + owner、vector、Actor、Goal、Topic、关键词和 1-2 hop Graph。
- 相似的其他 owner Memory 不得进入候选、Graph 扩展或最终结果。
- fixture 指标与 PostgreSQL 指标分开报告，不能用 Fake Embedding 结果冒充线上检索质量。

8. 最终复评和报告
- 先运行全量 pytest、Ruff、mypy、应用导入、前端既有门禁和密钥扫描。
- 先 dry-run 输出预计 Candidate/Judge/Embedding 调用、Token 上限和费用；未获得用户明确授权时不得运行任何真实复评，停在 dry-run 并向用户申请授权。
- 获得授权后才运行新的 live baseline；不得覆盖旧基线。
- 输出 before/after、剩余 Bad Case、人工仲裁、Judge 校准、PostgreSQL 检索和费用报告。
- 最终只保留 canonical live baseline、offline fixture 和必要验收文档；probe 中间报告不进入提交。

验收要求：
1. 原 47 Case 未被静默删除；Case 修订有版本和理由。
2. 15 个原 hard-failure Case 全部有离线 regression test 和明确问题归属。
3. owner/canary/internal literal leak 为 0。
4. endToEndSafetyFailure=0；非法 ID、evidence、time、departed、participant 和 world mutation 不能进入权威状态。
5. Memory 单次调用通过率 100%。
6. PostgreSQL owner-safe 评测集成通过，不再跳过该关键测试。
7. pytest、Ruff、mypy、前端现有 test/lint/build/E2E 全部通过。
8. 经授权的真实复评目标：first-attempt Schema >= 90%，最终 Schema >= 95%；安全/权威 hard failure=0；direct-question pass=100%；PostgreSQL Precision@K >= 0.75、Recall@K >= 0.90、owner 越界=0。
9. Judge 校准 >= 80% 且 Injection=3/3 才能声明 Judge 可作为质量门；否则如实标记 advisory，不伪造通过。
10. 未达到的目标保留为 Bad Case 和后续项，不能放宽规则以宣称完成。

明确不做：
- 不换 Candidate 模型，不增加多模型复核、SFT/RL、MCP/A2A、Redis、消息队列、Kubernetes 或新 Agent 框架。
- 不修改 Day1-Day7 玩法、人设、Goal、章节阈值和成功分支。
- 不重写 RunService、数据库或前端。
- 不顺手处理 CI、部署、并发优化、视觉资源或 API Response Model；它们属于后续 Goal。
- 不提交真实密钥、数据库 URL、完整生产 Prompt、coreSecrets、私有 Memory 正文或未脱敏 Trace。

交付物：
- project/AGENT_SEMANTIC_REMEDIATION_DESIGN.md
- 问题归属矩阵和脱敏 regression fixtures
- 评测投影/RuleScorer/Candidate/Judge 的最小修复与测试
- Judge Rubric v2 和校准报告
- PostgreSQL 检索集成报告
- 新旧 baseline before/after 报告
- project/AGENT_SEMANTIC_REMEDIATION_ACCEPTANCE.md
- 清晰、单一范围的 Git 提交；不得混入无关工作或 probe 报告

工作方式：先设计和离线诊断，再实现最小修复，再运行离线门禁。不要创建或调用子智能体，除非用户另行明确要求。真实网络和费用步骤必须在 dry-run 后获得用户明确授权。完成后报告实际测试结果、修复前后指标、仍未关闭的 Bad Case、Judge 是否仍为 advisory、PostgreSQL 集成状态和提交信息。不要替用户创建或启动新的 Goal，用户会自行设置。
```
