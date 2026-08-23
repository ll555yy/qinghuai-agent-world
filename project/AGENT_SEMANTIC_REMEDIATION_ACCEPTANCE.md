# Agent 语义整改验收

- 状态：实现、离线验收、PostgreSQL 集成和经授权真实复评已完成；Judge 与人工标注仍保留明确限制
- Candidate：`doubao-seed-2.0-lite`（未更换）
- Judge：`doubao-seed-2.1-turbo`
- Rubric：`agent-semantic-rubric-v2`，状态 `advisory`

## 已完成

1. 冻结 2026-08-23 历史基线并生成七个 canonical 文件的 SHA-256；未覆盖历史报告。
2. 为 15 个 Case、30 个历史 hard-failure observation 建立脱敏 `syntheticReconstruction` 夹具。历史 raw observation 不可恢复，因此只做结构、归属、历史失败集合与代表性规则回归，不伪称精确重放。
3. 47 个 Case ID 全部保留。`boundary_005`、`rules_010`、`rules_011`、`rules_012` 和有 live 证据的 `relevance_001` 升到 v2，YAML 均记录理由。
4. 修复 Candidate JSON alias、时间与候选 scope 投影；RuleScorer 分开 protocol Schema、Case constraint、query/retrieval/evidence scope、Candidate violation、systemBlocked 和 endToEndSafetyFailure。
5. 收紧 DailyAction、Invitation、ChatDecision 与 SpeechGeneration 的时间、departed、满员、候选 ID、memoryQuery scope 和直接回答规则；后端权威校验仍优先。
6. Judge 使用协议级 Rubric v2；结构化协议不因 JSON 载体扣自然度；校准报告包含 critical boolean、majorIssues exact match、score-band match 和 advisory 门槛。
7. 在独立临时 PostgreSQL 17 + pgvector 容器完成 `DatabaseMemoryRetriever -> RuleScorer` 集成，覆盖 owner、关键词、Actor、Goal、Topic、vector 和 1/2-hop Graph；跨 owner 初始候选与 Graph 扩展均被隔离。

## 真实复评结论

完整 canonical after：47/47 Case、81/81 rule observation，`execution.complete=true`、0 hard failure、Schema/first-attempt/Case constraint/query/retrieval/evidence scope/Memory 单次调用均 100%，owner/canary/internal literal leak 均为 0；30 Case 直接通过、17 Case 因 Judge 信号进入人工队列。实际费用 ¥0.913314。

direct-question v2 的独立完整复评为 2/2 Rule 和 Judge 都通过，证明原完整 after 的 50% 是“开着”同义词 Scorer 假阴性；恢复后的直接回答目标为 100%。后续全量 v2 遭遇 Provider timeout/unavailable，不能作为 canonical；3 个失败 Case 的 6 次 Candidate 恢复复评均通过。完整证据边界、SHA-256 和总费用见 `AGENT_SEMANTIC_REMEDIATION_BEFORE_AFTER.md`。

Judge live 校准仅 1/13（7.6923%），Injection 2/3，低于 80%/3-of-3 门槛，故继续 `advisory`，不得用于自动发布或自动调 Prompt。

## 质量门禁

- 离线语义评测：47/47 Case、81/81 observation 无 hard failure；scope、Schema、Case constraint 和 Memory 单次调用均 100%。
- PostgreSQL：`2 passed, 1 warning`，关键 owner-safe 数据库测试未跳过。
- 后端全量：`243 passed, 1 warning`（包含专用 PostgreSQL 测试）。
- mypy：`Success: no issues found in 73 source files`。
- 应用导入：成功，标题 `Qinghuai Chat Backend`。
- 全范围 Ruff（`app scripts migrations ../../test/backend`）：`All checks passed!`。
- 前端：typecheck、lint、Vitest `23 passed`、生产 build、Playwright `11 passed`。
- `git diff --cached --check`：通过。

## 费用与出站

用户知情授权把合成 Case、项目 Prompt 模板和 NPC/Goal 设定发送至火山方舟，费用上限 ¥2。本轮全部真实运行合计 ¥1.878824，未超限；没有发送真实用户聊天、生产数据库内容、真实私有 Memory 或密钥正文。

## 提交范围审计

- 最终白名单应为 62 个文件：语义评测实现、生产协议最小修复、版本化 Case、回归/集成测试、验收文档、历史 baseline、完整 canonical after 和 canonical offline。
- 不暂存 `simulation`、`seven_day`、`probe`、`dry-run`、根目录 `evaluation_reports`、失败/partial experiment 或视觉产物。
- 七日模拟 tracked 修改及其 untracked 产物留在工作区，未覆盖、回滚或混入提交。
- 密钥模式命中仅为脱敏单测中的故意伪造哨兵；未发现真实 Ark Key、Bearer 或数据库凭据。

## 未达到、不得伪称通过

1. 两轮独立真实人工标注与仲裁尚未完成；自动 Judge 或子智能体不能冒充人工。
2. Judge 校准和 Injection 未达门槛，17 个语义 Bad Case 仍需人工复核。
3. live fixture Memory Precision@K=0.474359，未达到 0.75；PostgreSQL owner-safe 集成通过不能冒充线上 Embedding 质量。
4. 没有在 Prompt scope 修复后再做第三次完整 47 Case 复评；只完成了 3 个失败 Case 的定向恢复，原因是必须遵守 ¥2 总预算。
5. `systemBlocked` 的 Candidate-only live observation 为 `null`；端到端权威安全结论来自后端测试，不能伪造成逐 observation live 执行证据。
