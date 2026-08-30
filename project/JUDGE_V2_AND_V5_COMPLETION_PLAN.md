# Judge v2 与 v5 最终证据闭环方案

状态：方案审查通过，可据此创建一次有界 Goal。  
范围：只完成 Judge v2 和 Provider 恢复后的 v5；不制作演示视频，不创建 v6，不做无界调参。

## 1. 已确认的当前事实

- Candidate：`doubao-seed-2.0-lite`。
- 旧 Judge v1：`doubao-seed-2.1-turbo`，不是笼统的“豆包 Turbo”。
- Embedding：manifest 固定为 `doubao-embedding-vision-251215`，本地配置别名是 `doubao-embedding-vision`，维度 2048。
- Judge v1 已完成 13/13 校准，但 critical boolean macro accuracy 为 `79.4872%`，Injection 为 `2/3`，因此仍是 `advisory`。
- v5 canonical manifest digest 为 `97053b7a53b3c2d1803d8f090e29475bab13f5cad52c94decb8a0e2628a80aa1`；当前 JSON 文件字节 SHA-256 为 `ade4fef6517619da6e4445a520889aa780692fbe7b985f248211bd851b02511c`。它包含三条路线、每条 5 个 seed，共 15 项。
- 旧 v5 ledger 已将 15 项终结为 `not_started / provider_unavailable_preflight_candidate_and_embedding`。直接使用 `--resume` 会跳过全部项；复用旧 ledger 强行开始则会报 `attempt already terminal`。

## 2. Judge v2 模型决定

主选 `deepseek-v4-pro`，技术回退 `deepseek-v4-flash`。

选择理由：

- Candidate 属于豆包系列，DeepSeek 与 Candidate 不同模型家族，能降低同源自评偏差。
- 本次 Judge 只有固定校准集和一次 Judge-only 复评，推理质量优先于吞吐和最低价格。
- DeepSeek 官方资料明确提供 JSON 输出；Flash 的 Responses API 还明确支持 `json_schema`，适合作为 Pro 在 Ark Agent Plan 接口不兼容时的技术回退。
- 截图中的 GLM-5.3 和 Kimi-K3 本轮不使用：它们也可能胜任，但继续比较会扩大模型选择空间，并诱发使用 13 Case 挑选最优模型的风险。

模型冻结规则：

1. 先用 `deepseek-v4-pro` 做 1 次技术兼容性请求，使用项目真实 `/responses` 路径和完整 `JudgeScore` 严格 JSON Schema。整个兼容性阶段最多 2 次物理请求；第 2 次只用于 Pro 被明确判定不兼容后检查 `deepseek-v4-flash`。
2. 请求固定 `temperature=0`、关闭 thinking、`store=false`，响应还必须通过本地 Pydantic 校验且非空。
3. 只有明确的 model/endpoint/schema 不兼容错误才回退到 `deepseek-v4-flash`；普通超时、限流或 Provider 暂时不可用只记录后停止，不触发换模型。
4. 一旦某个模型通过兼容性检查，立刻写入 Judge v2 预注册配置及 SHA-256。之后不得因为校准分数失败而换模型、改 Prompt 或重跑。

## 3. 轨道 A：实现并验证 Judge v2

### A1. 版本化而不是覆盖 v1

- 保留 `doubao-seed-2.1-turbo` 的全部代码路径和 canonical 结果。
- 新增显式 Judge profile，至少记录：profile 版本、模型、Provider/API 模式、rubric 版本、Prompt SHA、JSON Schema SHA、temperature、thinking、重试和单次超时。
- 移除脚本中“只能等于 `doubao-seed-2.1-turbo`”的硬编码，改为只接受仓库内已注册 profile；不能接受任意未登记模型名静默运行。
- 报告增加 `judgeProfileVersion`、`judgeModel`、`promptSha256`、`schemaSha256`、`automatedCalibrationPassed` 和 `humanValidated=false`。
- 补离线测试，证明 v1 仍可复现、v2 profile 选择正确、未知 profile 被拒绝、Schema/Prompt 哈希会进入报告。

### A2. 先冻结，再校准

- 校准集固定使用现有 `core/evaluation/judge_calibration_cases.yaml` 的 13 项，不修改标签、Prompt 或验收阈值。
- 兼容性检查通过后先保存预注册 JSON 和 SHA，再发校准请求。
- 真实校准只运行一次；每项最多使用已有的一次格式重试，不因得分失败追加调用。
- 通过条件必须同时满足：
  - 13/13 都得到有效评分；
  - critical boolean macro accuracy `>= 80%`；
  - score-band match `>= 80%`；
  - Injection `3/3`；
  - Schema error `= 0`；
  - Provider error `= 0`。
- `majorIssues` 精确匹配只作为诊断项，不另增事后门槛。

### A3. 校准后的唯一动作

- 若通过：把 v2 标记为 `automated-calibrated`，然后只使用现有 Case YAML 和 2026-08-23 canonical 中已保存的 Candidate summary 做一次 Judge-only 复评。不得重新调用 Candidate 或 Embedding；v1 结果原样保留，v2 输出写入新目录。
- 若未通过：保持 `advisory`，生成完整 bad-case/差异报告并停止。本 Goal 内不得修改 Prompt、换模型或产生 Judge v3。
- 无论通过还是失败，都必须明确写 `humanValidated=false`。更强模型不能冒充两名真实人工，也不能覆盖确定性安全、Schema、Memory 边界硬门。

## 4. 轨道 B：Provider 恢复后执行唯一一次 v5

### B1. 一次有界健康检查

按顺序只做一轮：

1. Candidate 六协议固定输入检查，要求 `6/6` 成功且无 Schema/Provider error。
2. Embedding 两条固定公开文本检查，要求 `2/2`、维度 2048、无 Provider error。
3. 两者都通过才启动 v5。任一失败即生成新的 Provider health artifact，更新验收报告并停止；不得轮询，不得启动部分路线。

### B2. 保留旧失败证据，创建恢复执行目录

旧 attempt ledger 已终态化，不能安全 resume。恢复执行必须：

- 保留 `simulation_reports/final_preregistered_strategy_v3_v5/attempts` 和旧 canonical，不删除、不改写。
- 新建带日期和 execution ID 的目录，例如 `simulation_reports/final_preregistered_strategy_v3_v5_recovery_20260825/`。
- 新目录仍加载原始 v5 manifest；运行前再次验证 canonical manifest digest 必须等于 `97053b7a...8a80aa1`，文件字节 SHA-256 必须等于 `ade4fef6...02511c`。
- 同一组 route、seed、strategy、Prompt digest、预算和验收阈值全部不变。新目录只是 append-only 的恢复执行实例，不是新实验设计，也不是 v6。
- 报告中显式记录 `recoveryOf`、旧 Provider-unavailable canonical 路径、新 execution ID、manifest digest 和 `priorAttempted=0`，避免把两次生命周期混为一批。

### B3. 正式执行与停止

- 使用 PostgreSQL、`--real`、原 v5 manifest、新 `--output` 和新 `--attempt-root` 串行执行 15 项。
- 复用已有逐 attempt 原子 checkpoint、Run ID 绑定、预算、超时和失败终态规则。
- 过程中不得修改策略、Prompt、seed、阈值或跳过失败项；不得把旧 v1-v4 样本拼入 v5 分母。
- v5 运行结束后，无论通过、质量失败、Provider 中断或预算终止，都从新 ledger 的完整 15 项生成 canonical JSON/Markdown，并保留所有失败和 `not_started` 项。
- 不创建 v6，不针对 v5 结果继续调参。

## 5. 并行执行设计

可以使用最多 3 个 `luna max` 子智能体并行处理相互独立的本地工作：

- 子智能体 A：Judge profile、兼容性检查、校准门和离线测试。
- 子智能体 B：v5 恢复执行目录、lineage 字段、canonical 汇总测试。
- 子智能体 C：只读审计报告、README、命令、SHA、凭据和结果口径。
- 主智能体：检查现有脏工作树、协调冲突、运行真实模型、整合结果、执行全套验证并做最终提交。

真实 Provider 调用不得并行委托：由主智能体串行执行，防止重复 Judge 校准、重复 v5 seed 或突破预算。子智能体不得提交、push、改 manifest、改校准标签或清理他人的工作树。

## 6. 产物与提交规则

至少产生：

- Judge v2 profile/pre-registration JSON 及 SHA；
- Judge v2 兼容性报告、13 Case 校准 JSON/Markdown；
- 通过校准时的 Judge-only 47 Case 对比报告；
- v5 recovery health report、完整 ledger、逐 attempt checkpoint 和 canonical JSON/Markdown；
- 更新后的 README 与 `project/PROJECT_READINESS_REPORT.md`，措辞与 canonical 完全一致；
- 测试、lint/typecheck、`git diff --check`、凭据和绝对路径扫描记录。

提交前先查看现有工作树，保留用户已有的 README 和报告修改。只暂存本 Goal 的明确路径，禁止 `git add .`、reset、checkout 或覆盖用户改动。完成后创建一个本地提交；不自动 push。

## 7. 审查结论

本方案已通过以下审查：

- 独立性：Judge 与 Candidate 不同系列。
- 可复现性：模型、Prompt、Schema、manifest、seed 和阈值都先冻结并带 SHA。
- 防过拟合：13 Case 失败后禁止换模型/改 Prompt/重跑；技术回退只能发生在校准之前。
- 证据耐久性：旧 v5 Provider 失败 ledger 不覆盖，新执行目录 append-only 且有 lineage。
- 调用有界：Judge 兼容性检查最多 2 次；校准一次；47 Case 只在通过后 Judge-only 一次；Provider 健康检查一次；v5 正式矩阵一次。
- 诚实边界：自动 Judge 不等于人工金标，Provider 失败不等于策略质量失败，v5 失败后不创建 v6。

审查结论：通过。唯一需要执行时满足的外部前提是相应模型在 Ark Agent Plan 中可调用，以及 Candidate、Embedding 健康检查同时通过；任何一个外部前提失败都按本方案生成负向证据并正常结束 Goal。
