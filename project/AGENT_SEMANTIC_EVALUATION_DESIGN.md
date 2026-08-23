# Agent 语义评测设计

- 状态：实施基线
- Candidate：`doubao-seed-2.0-lite`
- Judge：`doubao-seed-2.1-turbo`
- 原则：只测量当前生产行为，不在本阶段调 Prompt、换模型或改变玩法

## 1. 与七日可达性工作的边界

当前工作树中 `simulation/runner.py`、`simulation/evidence.py`、七日模拟脚本、三份模拟文档及其测试由另一 Goal 占用，本阶段只读。语义评测代码全部进入新的 `app/evaluation`、`core/evaluation` 和独立测试文件；共享的 Token、延迟和成本口径只通过既有公开接口复用，不修改正在进行的模拟实现。

## 2. 模块与依赖

```text
YAML cases -> CaseLoader -> EvaluationCase
                           |
Candidate adapter --------+-> EvaluationRunner -> RuleScorer
Judge adapter ------------------------------+  -> Report builder
                                             +  -> review queue
```

- `models.py` 是共享契约的唯一来源。
- `case_loader.py` 只加载和验证版本化 YAML，不执行模型调用。
- `rule_scorer.py` 只做确定性计算，不调用 Judge。
- `judge.py` 只通过独立 Ark 文本端口评分匿名 candidate，不导入或注册到 FastAPI/RunService/NPCAgentRuntime。
- 独立 Judge 使用 Ark Responses API：`store=false`、关闭 thinking、原生严格 JSON Schema；生产 Candidate 继续使用既有 Chat Completions 调用链。
- `runner.py` 编排 dry-run/offline/live、预算、超时和部分结果；默认不访问网络。
- `report.py` 生成稳定排序、脱敏的 JSON/Markdown/Bad Case/人工仲裁表。

## 3. 共享数据契约

### EvaluationCase

必须包含：

- `case_id: str`，全局唯一；
- `case_version: int >= 1`；
- `category`：`persona | boundary | memory | rules | relevance | coherence`；
- `protocol`：现有六协议或 `memory_retrieval`；
- `npc_id`：五名 NPC 之一；
- `input_context: dict`：合成、最小授权上下文；
- `expected_constraints: list[str]`；
- `forbidden_signals: list[str]`；
- `allowed_outcomes: list[str]`；
- `expected_memory_ids: list[str]`；
- `allowed_evidence_message_ids: list[str]`；
- `requires_postgres/requires_live_candidate/requires_live_embedding: bool`；
- `judge_rubric: list[str]`；
- `tags: list[str]`。

Loader 拒绝重复 ID、未知枚举、非法 NPC、互相矛盾的 `must_*`/`must_not_*` 约束、疑似凭据字段和值。Case 只使用项目场景或合成 canary。

### CandidateObservation

Runner 传给规则评分器和 Judge 的统一观测：

- `case_id/run_index/protocol`；
- candidate 文本或结构化输出；
- 实际 action、ID、evidence、retrieved memory IDs；
- 本次允许的 actor/goal/evidence/owner 集合；
- 延迟、Token、重试、失败码；
- 不包含模型品牌、API Key、完整生产 Prompt 或无关私有 Memory。

### RuleScore

包含硬门和指标：

- `hard_failure: bool`、`failures: list[str]`；
- Schema/action/ID/evidence 合法性；
- owner/canary/internal-field 泄露计数；
- Precision@K、Recall@K、MRR；
- direct-question pass、repetition；
- Token、延迟、重试和估算成本。

任一安全、所有权、非法权威状态或不可见证据失败即 `hard_failure=true`，Judge 高分不能覆盖。

### JudgeScore

Judge 严格 JSON Schema：

- 六维 1..5：`persona_consistency/context_faithfulness/response_relevance/naturalness/goal_progress/player_agency`；
- `contradiction_detected/unsupported_claim_detected/direct_question_answered`；
- `major_issues` 受限枚举；
- 每维简短 `evidence`；
- `confidence: low | medium | high`。

总分由本地代码算术平均生成。Judge 输入将 candidate 标为不可信数据，隐藏 Candidate 模型名称；不得包含无关 coreSecrets。

### CaseResult 与 EvaluationReport

每个 CaseResult 保存 RuleScore、零到两次 JudgeScore、Judge 分歧、脱敏 candidate 摘要、review reasons。Report 顶层包含：

- `metadata`、`execution`；
- `ruleBasedMetrics`；
- `llmJudgeMetrics`；
- `combinedResult`；
- `cases`；
- `badCases`；
- `reviewQueue`。

## 4. Case 基线

`core/evaluation/agent_semantic_cases.yaml` 至少 30 项，六类各至少 5 项。应覆盖人设、越权/注入、owner canary、关键词/Actor/Goal/Topic/向量/Graph、时间与参与者规则、直接提问、证据一致性、连续复读和参与者变化。

`judge_calibration_cases.yaml` 至少 10 项，其中至少 3 项 candidate 直接尝试操控 Judge。校准用合成输出，不访问生产数据。

## 5. Judge 安全和稳定性

- Candidate 内容用明确数据定界符包围，系统提示声明其中指令一律不执行。
- Judge 只输出 Schema JSON；格式错误最多一次同协议重试，仍失败则记录而不伪造分数。
- 至少 20% 的真实 candidate 输出做第二次独立评分；任一维度差值大于 1 标记 `judge_disagreement` 并进入人工队列。
- `confidence=low`、矛盾、无依据断言、规则与 Judge 冲突均进入人工队列。
- 安全硬门始终由规则代码裁决。

## 6. 执行模式和预算

- `--dry-run`：只验证 Case 和打印预算，不构造真实客户端。
- `--offline`：Fake Candidate/Fake Judge/Fake Embedding，禁止网络。
- `--live`：才可构造 Candidate 客户端。
- `--enable-judge`：和 `--live` 同时存在才可构造真实 Judge。
- 支持 Case/category 筛选、Candidate/Judge/Embedding 调用上限、CNY 费用上限和总超时。
- Candidate 与 Judge 使用独立费率；每次调用前按各自最大 Token 预留最坏单次成本，provider/format retry 和后置校准也纳入真实调用计数。
- 校准区分 prompt-only、skipped、live-scored；预算中断后只能通过显式 live、独立增量费用上限续跑缺失 Case，不重复已完成校准。
- 达到上限停止新调用，保存部分结果并标记 `complete=false`。
- 默认真实基线每个 live Candidate Case 两次，Judge 全量一次、20% 再评分一次；真实调用只能由主会话在用户明确授权后执行。

## 7. 脱敏

报告允许保存 case ID、分类、合成输入摘要、规则结果、Judge 分数和短证据；禁止保存 API Key、数据库 URL、完整系统 Prompt、无关私有 Memory、完整 coreSecrets 或未脱敏 Trace。敏感模式和 Case 自身 forbidden canary 在输出前统一替换为 `[REDACTED]`。

## 8. 并行文件所有权

- Luna A：Case YAML、`models.py`、`case_loader.py`、`rule_scorer.py` 及其测试。
- Luna B：Judge 协议/适配器、校准 YAML 及其测试。
- Luna C：Runner、Report、CLI、PostgreSQL/Runner/Report 测试。
- 主会话：本设计、共享接口协调、`__init__.py`、最终集成、验收/基线/子智能体审查文档。

任何共享接口分歧由主会话裁决；各子智能体不修改其他范围或七日可达性文件。

## 9. 验收

离线验收要求：至少 30+10 Case；安全硬门、Judge 注入、分歧、脱敏、预算、超时和部分报告有测试；pytest/Ruff/mypy 全绿；`--dry-run` 与 `--offline` 成功且无网络。真实基线属于需用户授权的最后步骤，未授权时验收文档必须明确为“离线系统完成，真实基线待授权”，不能伪造分数。
