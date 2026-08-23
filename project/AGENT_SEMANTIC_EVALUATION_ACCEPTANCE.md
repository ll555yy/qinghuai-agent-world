# Agent 语义评测验收

- 状态：实现、离线验收和真实完整基线均已完成
- Candidate：`doubao-seed-2.0-lite`
- Judge：`doubao-seed-2.1-turbo`
- Rubric：`agent-semantic-rubric-v1`

## 验收范围

- 47 个版本化 Case，六类分别为 persona 6、boundary 6、memory 11、rules 12、relevance 6、coherence 6。
- 13 个严格校准 Case，其中 3 个 Judge Injection Case。
- dry-run、offline、live、显式 Judge 开关、Case/category 筛选、Candidate/Judge/Embedding 调用上限、独立费率、单次和总超时、费用硬门、partial 保存和校准续跑。
- JSON、Markdown、Bad Case、人工仲裁、Judge 稳定性、Judge 校准产物。
- Schema/action/actor/goal/evidence/owner/canary/internal field/time/departed/participant/world mutation/Memory 单次调用硬门；高 Judge 分不能覆盖硬失败。

## 调用与安全边界

- dry-run 不构造 Provider；offline 只使用 Fake Adapter，自动化测试禁用真实网络。
- 只有 `--live --enable-judge` 才构造真实 Judge；校准续跑还必须单独显式 `--live` 和增量费用上限。
- Candidate 继续复用生产 `DecisionService` 六协议、Schema、重试和温度；未修改生产模型、Prompt、Persona、Goal、玩法或世界状态。
- Judge 使用 Ark Responses API、`store=false`、关闭 thinking、原生严格 JSON Schema；只接收合成 Case、必要 Persona/Goal 最小投影、授权上下文和匿名 Candidate。
- Candidate 与 Judge 使用独立费率和可选独立 Key；真实 Key 不进入源码、Case、日志或报告。
- provider retry、格式 retry、校准请求均计入实际 Judge 调用数和统一费用/超时执行状态。
- 报告写盘前通过严格 `EvaluationReport` 校验，并对嵌套 `candidateSummary` 中的 `coreSecrets`、`ownerNpcId`、`trace_id`、Prompt、Memory 和凭据模式脱敏。

## 主会话实测

- 全仓 pytest：`219 passed, 11 skipped, 1 warning`。11 个跳过均要求专用 `QINGHUAI_TEST_DATABASE_URL`，其中包含真实 PostgreSQL Memory owner-safe 评测集成。
- Ruff 精确命令：`All checks passed!`
- mypy：`Success: no issues found in 73 source files`
- dry-run：成功，47 Case；计划 Candidate 68、主 Judge 82、Embedding 12。开启真实校准时再加 13 个逻辑 Judge Case。
- offline：成功，47/47；Fake Candidate 34、Fake Judge 34、Fake Embedding 12；0 网络、0 费用。
- live：成功完成 47/47；Candidate 68、主 Judge 82、校准 13 Case/14 请求、Embedding 12；`execution.complete=true`。

用户只授权修改三个七日测试文件的 import 排序；主会话未改其测试语义，也未覆盖 simulation/evidence/七日结果文件中的另一会话工作。

## 真实基线结论

完整指标和费用账见 `AGENT_SEMANTIC_EVALUATION_BASELINE.md`。当前真实结果不是“高分验收”：81 个规则 observation 中有 30 个 hard failure，24 个 Case 进入 Bad Case/人工队列；Judge 校准只通过 2/13，Injection 通过 2/3。系统验收通过的含义是评测器能诚实、可重复、受预算约束地暴露这些问题，不是 Candidate 或 Judge 已达到生产质量门槛。

## 尚未关闭

1. 缺少 `QINGHUAI_TEST_DATABASE_URL`，真实 PostgreSQL `DatabaseMemoryRetriever -> RuleScorer` 集成仍跳过；fixture Memory 指标不能冒充线上检索质量。
2. Judge 校准质量不足，六维分数需配合人工仲裁，不能自动驱动生产 Prompt 调优。
3. Candidate 的 Schema/ID scope、直接提问、Goal 推进和玩家自主性暴露明显不足；本 Goal 按“先测量、不调参”原则只记录，不实施 Prompt 优化。
