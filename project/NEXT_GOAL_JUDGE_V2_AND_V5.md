# 可直接创建 Goal 的提示词

```text
目标：在不覆盖既有证据、不产生 v6、不进行无界调参的前提下，完成本项目剩余的自动化面试证据闭环：实现并校准 Judge v2；在 Candidate 与 Embedding 恢复后，对原预注册 v5 执行唯一一次可审计的恢复运行；整理 canonical 结果、README、最终验收报告和一个本地提交。

先完整阅读并严格执行：
- project/JUDGE_V2_AND_V5_COMPLETION_PLAN.md
- project/PROJECT_READINESS_REPORT.md
- project/SEVEN_DAY_SIMULATION_GUIDE.md
- core/backend/app/simulation/manifests/final_agent_validation_strategy_v3_v5.json
- 当前 Judge、evaluation runner/report、simulation runner/manifest/evidence 及相关测试

开始时先检查 git status、diff、当前提交和现有 ignored evidence。现有工作树可能包含用户修改；必须保留并在冲突处人工整合。禁止 reset、checkout、clean、覆盖旧 canonical、删除 ledger、git add . 或提交无关文件。

执行方式：
1. 允许使用最多 3 个 luna max 子智能体并行处理相互独立的本地任务：
   - A：Judge v2 profile、兼容性检查、校准门和离线测试；
   - B：v5 recovery execution lineage、独立 output/attempt root、canonical 汇总和测试；
   - C：只读审计 README、报告、SHA、命令、凭据和措辞。
   主智能体负责协调、合并、真实模型调用、全套验证和提交。子智能体不得运行真实 Provider、提交、push、改 manifest/seed/阈值/校准标签或清理工作树。
2. 用户已授权本 Goal 范围内所需的真实模型调用，无需再次询问授权；但所有真实调用必须由主智能体串行、有界执行并记录 calls/tokens/latency/cost。不得扩大到本 Goal 之外。

Judge v2：
1. 保留 Judge v1 `doubao-seed-2.1-turbo` 的代码和 canonical 结果。
2. 主选 `deepseek-v4-pro`。先通过 Ark Agent Plan 的真实 `/responses` 路径，以完整 JudgeScore 严格 JSON Schema 做 1 次技术兼容性检查；固定 temperature=0、thinking disabled、store=false，并进行本地 Pydantic 校验。整个兼容性阶段最多 2 次物理请求，第 2 次只用于 Pro 明确不兼容时检查 Flash。
3. 只有明确的 model/endpoint/schema 不兼容才允许在校准前回退一次到 `deepseek-v4-flash`。超时、限流或 Provider 暂时不可用应记录后停止，不能换模型。不得使用 13 Case 得分挑模型。
4. 兼容模型确定后，先写 versioned Judge profile/pre-registration，记录模型、API、rubric、Prompt SHA、Schema SHA、temperature、thinking、重试、超时和日期；写入后冻结。
5. 修改旧硬编码，使脚本只接受仓库注册的 v1/v2 profile；未知 profile 必须失败。补足离线单测。
6. 原样运行现有 13 个校准 Case 一次。通过必须同时满足：13/13 完成、critical boolean macro accuracy >=80%、score-band match >=80%、Injection 3/3、Schema error=0、Provider error=0。majorIssues 只作诊断。
7. 若通过，只使用现有 Case YAML 和 2026-08-23 canonical 中已保存的 Candidate summary 做一次 Judge-only 复评，不得重调 Candidate/Embedding；保留 v1 并生成 v2 对比 canonical。若失败，保持 advisory，生成完整 bad-case/差异报告后停止，不改 Prompt、不换模型、不重跑、不创建 Judge v3。
8. 所有结果必须写 humanValidated=false。确定性安全、Schema、Memory 边界仍是发布硬门；禁止把模型称为两名真实人工。

v5：
1. 只执行一轮健康检查：Candidate 六协议必须 6/6；Embedding 两条固定公开文本必须 2/2、2048 维。任一失败即生成新的 health artifact、更新 README/验收报告并正常结束；不轮询、不启动部分路线。
2. 两者通过后，再验证原 v5 canonical manifest digest 必须为 97053b7a53b3c2d1803d8f090e29475bab13f5cad52c94decb8a0e2628a80aa1，当前 JSON 文件字节 SHA-256 必须为 ade4fef6517619da6e4445a520889aa780692fbe7b985f248211bd851b02511c。
3. 旧 v5 ledger 已终态化，不能使用旧 --resume，也不能覆盖。创建带日期/execution ID 的新 output 和 attempt-root，但严格使用原 v5 manifest、原 route/seed/strategy/Prompt digest/预算/阈值。记录 recoveryOf、旧 Provider-unavailable canonical、新 execution ID、manifest digest、priorAttempted=0；这是同一 v5 的恢复执行实例，不是 v6。
4. 使用 real + PostgreSQL 串行执行完整 15 项。复用逐 attempt 原子 checkpoint、Run ID 绑定、超时、预算和失败终态；不改策略、不调参、不换 seed、不漏失败项、不拼接 v1-v4 样本。
5. 无论通过、质量失败、Provider 中断或预算终止，都从新 ledger 的完整 15 项生成 canonical JSON/Markdown，更新 README 和 project/PROJECT_READINESS_REPORT.md。停止后不得创建 v6 或继续调参。

验证与停止条件：
- 运行新增/相关 pytest，再运行仓库现有后端 pytest、Ruff、mypy、前端 lint/typecheck/test/build 和适用的 Playwright/数据库门禁；因环境无法运行的项目要给出准确原因，不能写成通过。
- 对所有新 JSON/Markdown 校验 SHA、内部路径、分母、调用数、Token、成本和 README/报告一致性；扫描真实凭据、私密 Prompt/Memory、绝对本机路径；执行 git diff --check。
- 不把 Provider 不可用写成策略失败，不把 incomplete 写成通过，不伪造 Token/成本，不伪造人工证据。
- 结果不论 pass/fail/provider-unavailable 都是本 Goal 的有效终态；完成证据整理后停止，不等待无限恢复。
- 只暂存本 Goal 明确涉及的路径并创建一个本地提交，不 push。提交后复核 status；若用户原有未提交改动仍存在，明确列出而不要纳入或删除。

最终汇报必须包含：实际选择的 Judge 模型及原因；兼容性与 13 Case 指标；是否执行 47 Case Judge-only；v5 health 和 15 项全分母；所有 canonical 路径与 SHA；测试结果；真实调用/Token/成本；本地提交 hash；仍需真实人工完成的边界。不要只汇报过程，要给出明确终态。
```
