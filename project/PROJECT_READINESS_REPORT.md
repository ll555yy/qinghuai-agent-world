# 项目就绪度与验证报告

- 状态：语义、检索和 CI/E2E 已通过；Judge v2 已完成一次冻结校准并因 Injection `2/3` 保持 advisory；原预注册 v5 已在独立 recovery execution 中完成 `15/15` 全分母并达到 manifest 聚合门槛
- 冻结起点：`50d6c88`
- 七日基线提交：`738ef11`
- 预注册提交：`235b36b`
- 恢复批次预注册提交：`9f5cae1`
- 第二恢复批次预注册提交：`5f1f497`
- strategy v2 holdout 预注册提交：`aa71928`
- v4 失败证据、strategy v3 与恢复修复：`e9a4a52`
- strategy v3 holdout 预注册提交：`15f0dd7`
- 验证基线：完成 v5 Provider 结论、语义闭环与 CI/E2E 收口的提交

## 1. 可审计提交链

| 阶段 | 提交 | 证明内容 |
|---|---|---|
| 语义整改 | `50d6c88` | 47 Case 评测框架、整改和历史 canonical after |
| 七日基线 | `738ef11` | 当前路线、证据汇总器、测试、历史结果和冻结清单 |
| 预注册 | `235b36b` | manifest、连续 seed、策略/代码哈希、预算与失败规则先于真实运行提交 |
| 恢复批次预注册 | `9f5cae1` | 新实验 ID 与连续 seed、逐 attempt 原子检查点先于 v2 真实调用提交 |
| 第二恢复批次预注册 | `5f1f497` | v3 独立实验 ID、连续 seed `20260850..20260854` 与 digest 先于真实调用提交 |
| strategy v2 holdout 预注册 | `aa71928` | v4 连续 seed `20260855..20260859`、混合策略 digest 与原门槛冻结后再运行 |
| v4 失败证据与恢复修复 | `e9a4a52` | v4 canonical、pro_lin v3 状态拆分、Run ID 持久绑定和不中途重跑 seed 的 resume 逻辑 |
| strategy v3 holdout 预注册 | `15f0dd7` | v5 连续 seed `20260860..20260864`、策略/Prompt digest 与原门槛在真实调用前冻结 |
| 最终交付 | 本提交 | Judge v2 advisory、v5 recovery `15/15` canonical、调用/成本、CI/E2E、README 和本验收报告 |

## 2. Agent 语义与检索

Canonical 47 Case 是 2026-08-23 的单一 live 批次，没有拼接历史或局部恢复结果：

- 分母：47/47 Case、81 个 observation；`execution.complete=true`；Candidate 68 次、Embedding 12 次、Judge 96 次（含 13 次校准），总调用 176 次；
- 确定性规则：hard failure `0/81`，最终 Schema `100%`，首轮 Schema `100%`，直接提问规则通过率 `100%`，owner/canary/internal leak 均 `0`；
- 最终判定：47 Case 中 29 个直接通过，18 个进入人工复核；Judge 不能把 review 项升级为自动失败；
- 性能：Candidate P50 `3972.36 ms`，P95 `10371.531 ms`；
- fixture 检索（只验证评测投影）：strict Precision@K `0.474359`、Precision@returned `1.0`、Recall@K `1.0`、MRR `0.923077`；
- PostgreSQL 留出集：14 次真实 `DatabaseMemoryRetriever.search()`，tuning/holdout 各 7；holdout 有效排序查询 6 个，Precision@returned / Recall@K / MRR / strict Precision@K 均 `1.0`，FPR `0`，owner 越界 `0`、重复 `0`，空查询 `1/1`；
- 空查询整改：真实数据库审查发现空 MemoryQuery 会退化召回最近私有记忆，现已改为直接空集，并由 PostgreSQL 集成测试与留出集共同覆盖。

证据：

- [47 Case JSON](evaluation-results/live-final-canonical-2026-08-23/agent_semantic_evaluation.json)，SHA-256 `7ea816f3912f897fecc02b4484d80251fa99f732c4e23895d0f07b81d06f9b43`；
- [47 Case Markdown](evaluation-results/live-final-canonical-2026-08-23/agent_semantic_evaluation.md)；
- [PostgreSQL 检索 JSON](evaluation-results/postgres-retrieval-final-2026-08-23/postgres_retrieval_benchmark.json)，SHA-256 `886236ad5edb8737c0413e376dcd58deece578c2feac666b451cf3626d0d3aba`。

## 3. Judge 与人工边界

两份人工标注表只允许由两名相互独立的真实人工填写。在获得人工金标、分歧和仲裁前，Judge 保持 `advisory`；子智能体、Candidate、Judge 或重复模型调用均不计作人工。

真实 Judge 校准完成 13/13，无 provider/schema error，score-band match `92.3077%`；但 critical boolean macro accuracy `79.4872% < 80%`，Injection `2/3 < 3/3`，因此 `qualityGateStatus=advisory`。47 Case Judge 六维均分为 persona `3.918919`、context `3.646341`、relevance `3.439024`、naturalness `4.357143`、goal progress `3.144737`、player agency `4.036585`。这些数字只用于诊断和人工队列排序。

2026-08-25 的首次 Judge v2 完成尝试在 Codex 网络沙箱内返回项目归一化错误 `ai_provider_unavailable`。随后只读网络诊断证明：沙箱内 DNS 被映射到 `198.18.1.106` 且 443 连接失败，而沙箱外同一域名可达并对无认证 `curl` 正常返回 Ark `401/AuthN_MissOrInvalidAuthorizationHeader`；因此早期错误是沙箱出站隔离造成的 `APIConnectionError`，不是 Ark 的模型、端点、Schema、认证或额度结论。负向证据保留不覆盖。

在沙箱外，主选 `deepseek-v4-pro` 用完整严格 `JudgeScore` Schema 的 Ark `/responses` 路径一次通过兼容性检查：temperature `0`、thinking disabled、`store=false`，本地 Pydantic 通过，`1` 次请求、`1498` Token、`3758.828 ms`、无 Provider retry。由此冻结 `judge-v2` profile，未调用 Flash。固定 13 Case 原样运行一次并 `13/13` 得分：critical boolean macro accuracy `84.6154%`、score-band match `92.3077%`、Provider/Schema error `0`，但 Injection 仅 `2/3`，所以 `qualityGateStatus=advisory`。按预注册停止，不改 Prompt、不换模型、不重跑，也不执行 47 Case Judge-only；Candidate/Embedding 调用均为 `0`，`humanValidated=false`。

证据：[早期沙箱连接失败 JSON](evaluation-results/judge-v2-compatibility-provider-unavailable-2026-08-25/judge_v2_compatibility.json)，SHA-256 `7c2934e26b34a14fe56d6cbc18273aee33456b0c55077a80f9eb29e149709eb7`；[兼容性通过 JSON](evaluation-results/judge-v2-calibration-advisory-2026-08-25/judge_v2_compatibility.json)，SHA-256 `fd5072da3f5438c22b8c7a290b6f06f9efcd00be4f2aae4e94fbd635824ebbfc`；[13 Case 校准 JSON](evaluation-results/judge-v2-calibration-advisory-2026-08-25/judge_profile_completion.json)，SHA-256 `d56832275c518393bea858f8eabbaead6eaae453a5ba184cb8dca38d8a853da3`。

## 4. 预注册七日全分母

原批次 manifest 已在 `235b36b` 预先提交：observer / pro_lin / pro_zhao 共用连续 seed `20260840..20260844`，合计 15 个唯一 attempt。Ark Provider 连续超时后安全停止；ledger 已把所有计划项终态化，结果为 `planned=15`、`attempted=11`、`infraValid=10`、`gameplayPass=0`、coverage `0.733333`、`complete=false`。前 10 个已完成运行的丰富报告只存在于被中止进程内存，不能事后重建，因此 canonical 汇总保守地按缺失报告处理，不能用其推导玩法成功率。

为解决上述证据耐久性问题，runner 增加逐 attempt 原子 JSON/Markdown 检查点；恢复批次 manifest 在 `9f5cae1` 预先提交，使用独立实验 ID 和连续 seed `20260845..20260849`，没有替换或复用 v1 seed。真实网络健康检查与 Embedding 预检成功后开始 v2，但第二个 attempt 再次出现密集文本超时、Provider unavailable 和 Embedding 部分失败，因此再次安全停止。v2 全分母结果为 `planned=15`、`attempted=2`、`infraValid=1`、`gameplayPass=0`、coverage `0.133333`、`complete=false`；首个完整 observer attempt 的检查点已保留。两轮失败记录均保留，不能挑选合并成一个“15 局成功批次”。

Provider 后续短时恢复，两轮六协议检查均 `6/6` 一次通过且 Embedding `2/2` 后，v3 manifest 在 `5f1f497` 预先提交并开始真实运行。v3 完成了全部 5 个 observer 与 5 个 pro_lin，逐局检查点均成功落盘；第一个 pro_zhao attempt 随后进入大量连续 `ai_timeout`、`ai_provider_unavailable`、空响应和 Embedding 部分失败，按异常消费规则人工安全停止。ledger 终态为 `10 completed / 1 runner_failed / 4 not_started`，全分母结果 `planned=15`、`attempted=11`、`infraValid=10`、coverage `0.733333`、`complete=false`。11 个临时 PostgreSQL Run 均按精确 runId 删除，专用库从 25 条恢复为原有 14 条。

v3 的 5 个 pro_lin 均到达 `compromise_submitted`，但玩家任务为 `1 completed / 3 partial / 1 failed`，未达到预注册的“至少 2 个 completed”。根因是旧固定策略在 Day7 主动允许重复已满足条件并继续 conditional；历史 v1 策略保持冻结，新增 `strategy.pro_lin.v2` 仅澄清“已经写入的条件不再挂起，只有真实未满足事项才 conditional”。该修复已经离线测试，但尚未经过新的预注册 holdout，不能用 v3 回填或改判。

v4 holdout 在 `aa71928` 预注册后真实运行：5 个 observer、5 个 pro_lin、2 个 pro_zhao 完成；第 13 个 attempt 在 Provider 故障风暴中人工停止并记为 runner_failed，后 2 个为 not_started。ledger 全分母为 `planned=15`、`attempted=13`、`infraValid=12`、coverage `0.866667`、`complete=false`；13 个临时 PostgreSQL Run 均按精确 runId 删除，专用库恢复为原有 14 条。v4 的 pro_lin v2 结果为 `0 completed / 3 partial / 2 failed`，gameplay pass `3/5`、player completed `0/5`，两项均低于未修改的 `4/5` 与 `2/5` 门槛。pro_zhao 已完成两局中一局保持失败对照、另一局意外成为 partial，未达到 4/5。

只读检查 5 个 Day7 对话确认 v2 原文均成功送达，但同一条话术没有稳定更新两个独立状态：周仍把已写入的保护条件表达为 conditional，或没有形成授权。`e9a4a52` 新增 `strategy.pro_lin.v3`，把“青槐文社议案无条件支持”和“正式提交批准授权”拆成两个公开、非自适应动作；离线 Fake 模型必须通过真实 RunService/resolver 得到 `consensus_submitted`、`core_adopted`、`completed`。同一提交还让 attempt 创建 Run 后立即持久绑定 runId，resume 只复用已完成 checkpoint、把陈旧 started 终态化为 runner_failed，绝不重跑同一 seed。

v5 已在 `15f0dd7` 预注册：三路线使用全新连续 seed `20260860..20260864`，pro_lin 使用 v3，门槛和费用口径不变，manifest SHA-256 为 `97053b7a53b3c2d1803d8f090e29475bab13f5cad52c94decb8a0e2628a80aa1`。本收口 Goal 只执行一轮最小真实预检：Candidate 六协议 `0/6`，6 次物理请求均为 `ai_provider_unavailable`，无格式重试；Embedding 对 2 条固定公开文本发出 1 次物理请求，返回 `embedding_provider_error / APIConnectionError`。两者均无 Provider request ID 和 Token 用量。按预先规则不再轮询、不启动正式矩阵；ledger 已将 15 个 planned attempt 全部终态化为 `not_started / provider_unavailable_preflight_candidate_and_embedding`。v5 canonical 为 `planned=15`、`attempted=0`、`infraValid=0`、`gameplayPass=0`、coverage `0.0`、`complete=false`；这是外部 Provider 不可用结论，不是 strategy v3 质量通过或失败的样本证据。

在 `4eda615` 收口轮次中只执行了一轮 v5 恢复健康门。Candidate 六协议仍为 `0/6`；每个逻辑检查触发底层唯一一次瞬时 Provider retry，合计 `12` 个物理请求、`6` 个 retry，全部为 `ai_provider_unavailable`。Embedding 对 2 条公开文本发出 1 个批量物理请求，结果仍为 `embedding_provider_error / APIConnectionError`，实际维度和 Token 均不可得。健康门失败后没有轮询，没有复核 manifest digest，没有创建 recovery execution/ledger/PostgreSQL Run，也没有启动 15 项；这不是“重新运行 15 个”，旧 v5 ledger 与 `15/0/0/0` canonical 均保持不动。

随后用户明确要求重新执行健康检查。该独立追加轮次再次得到 Candidate `0/6`（`12` 个物理请求、`6` 个 Provider retry）和 Embedding `0/2`（`1` 个批量物理请求、`APIConnectionError`）；所有请求仍无 Provider request ID 和 Token 用量。此次复检没有覆盖前一份证据，硬门失败后同样未核验 manifest、未创建 execution/ledger/PostgreSQL Run，也未启动 15 项。

2026-08-25 在 Codex 网络沙箱外重新诊断后，Ark 域名和 443 可达；一轮冻结健康门得到 Candidate 六协议 `6/6`（6 次物理请求、0 retry、5,699 Token）和 Embedding `2/2`（1 次批量请求、57 Token、2048 维）。随后核验 manifest canonical digest `97053b7a53b3c2d1803d8f090e29475bab13f5cad52c94decb8a0e2628a80aa1` 与原始文件 SHA-256 `ade4fef6517619da6e4445a520889aa780692fbe7b985f248211bd851b02511c`，创建独立 recovery execution；原 `15/0/0/0` Provider-unavailable canonical 和所有更早失败证据保持不变。

恢复执行严格使用原 v5 的 3 路线、15 个 attempt、seed `20260860..20260864`、策略/Prompt digest、预算和阈值，未创建 v6。用户手工终止的旧第 3 次未完成记录经明确授权精确删除后，第 3 次从头运行；第 1、2 次 checkpoint 直接复用，没有重跑。最终 `planned/attempted/infraValid/gameplayPass=15/15/15/14`、coverage `1.0`、ITT `0.933333`，15 个 attempt 均 completed。observer `5/5` 零玩家发言；pro_lin `5/5` gameplay pass 且玩家任务 `5/5 completed`；pro_zhao 低投入失败对照 `4/5`，达到预注册的 `minimumFailureControlPasses=4`。seed `20260861` 为真实 partial，保留 `failure_control_changed_branch` 与 `failure_control_not_failed`，不删除、不改判。原始 runner 因“任一单局 qualityGateFailures”给出保守非零退出状态；canonical 按 manifest 的聚合阈值判断 `requirementFailures=[]`、`complete=true`，两层口径均保留。

15 个临时 PostgreSQL Run 均绑定唯一 runId 并在批次尾逐一精确删除；15/15 报告的 `repositoryRecovered=true`、`temporaryRunDeleted=true`。全批文本物理请求 `3,619` 次、Provider retry `98` 次、Prompt `11,041,344` Token、Completion `3,442,445` Token；Embedding `301` 次 / `84,118` Token；估算费用 `19.076491 CNY`。Schema 最终 `3,367/3,445 = 97.7358%`，首轮 `3,340/3,393 = 98.4380%`，安全降级结果 `78` 个；这些是上线韧性风险，不因统计门槛通过而隐藏。

Canonical 证据：

- [v1 中断全分母 JSON](simulation-results/final-preregistered-v1-interrupted-2026-08-23/seven_day_gameplay_evidence.json)，SHA-256 `df7163924460d3499799eadc45c9e1faeb3b57809e1a58f33767389e67c6f36c`；
- [v2 恢复批次中断全分母 JSON](simulation-results/final-preregistered-recovery-v2-interrupted-2026-08-24/seven_day_gameplay_evidence.json)，SHA-256 `891cf03ecdc0a7490d047c29e2235728b4324a21701ed950ede010884b50a6d1`；
- [v3 第二恢复批次中断全分母 JSON](simulation-results/final-preregistered-recovery-v3-interrupted-2026-08-24/seven_day_gameplay_evidence.json)，SHA-256 `9e2a7e07eeb7e49340631b06d12467d7c7d7d68357f05d5316f8be086b68e424`；
- [v4 strategy v2 中断全分母 JSON](simulation-results/final-preregistered-strategy-v2-v4-2026-08-24/seven_day_gameplay_evidence.json)，SHA-256 `00da5ba495be1e70885f6ee2a7b50ff2cf5d4761ba0d42a019811b1825e180c2`；
- [v5 Provider 不可用全分母 JSON](simulation-results/final-preregistered-strategy-v3-v5-provider-unavailable-2026-08-24/seven_day_gameplay_evidence.json)，SHA-256 `09573d47a05e35bca83f0d7643028c3bf16c9dcb535d2bc10b7d6c095b0aa677`；
- [v5 真实健康检查 JSON](simulation-results/final-preregistered-strategy-v3-v5-provider-unavailable-2026-08-24/provider_health_check.json)，SHA-256 `3fd207de11fdd20a48bd025be8d3ca5870079e0d88341b16e14f42ce1c3b576e`；
- [v5 恢复健康检查 JSON](simulation-results/final-preregistered-strategy-v3-v5-recovery-health-provider-unavailable-2026-08-25/provider_health_check.json)，SHA-256 `d61a3313472e32a5891d6577c00baa9fd6f4ca1d4018c0cfca2cc85ad5bffa4d`；
- [v5 用户要求的健康复检 JSON](simulation-results/final-preregistered-strategy-v3-v5-user-recheck-provider-unavailable-2026-08-25/provider_health_check.json)，SHA-256 `b12645372d1c67252db24aaadf49c1bec6f471837a6e5e1ba9438ae007f6eca2`；
- [v5 recovery 健康门 JSON](simulation-results/final-preregistered-strategy-v3-v5-recovery-2026-08-25/provider_health_check.json)，SHA-256 `d843aa47c29280eba3c4385bd7c4040896263b132bb0d9a7c40c7545dbac4e5b`；
- [v5 recovery canonical JSON](simulation-results/final-preregistered-strategy-v3-v5-recovery-2026-08-25/seven_day_gameplay_evidence.json)，SHA-256 `d3b613f70209bee27abfd3a3702a3bedc139a55608ff448572c43ceb67976155`；
- 历史汇总均保留各自 completed、runner_failed 与 not_started 原貌；最新 recovery canonical 只使用本次独立 execution 的 15 个原 v5 attempt，不拼接 v1-v4。

## 5. CI 与真实 full-stack E2E

无真实 Key 的 CI 已配置。测试 app 仅通过显式构造注入确定性 TextModel/Embedding，生产 app 不存在 fake 环境变量后门。黄金链路没有 `page.route` 或 WebSocket Mock，真实经过 React/Phaser、FastAPI、RunService、LangGraph、Repository、PostgreSQL/pgvector 和原生 WebSocket，覆盖 Run 创建、邀请、玩家消息、owner-safe Memory retrieval、NPC 回复、refresh、`afterSeq` replay、18:00 关日及持久化恢复。

本 Goal 最终复验：Vitest `52/52`、标准 Playwright `11/11`；full-stack Playwright 保留既有 `1/1` 证据，本轮因未设置专用 `QINGHUAI_TEST_DATABASE_URL` 未重跑。全栈测试曾先后暴露可交互 notice 遮挡和 Day1→Day2 reload 的合法时间竞态，均修正测试交互/断言后由完整黄金链路复验通过。

## 6. 成本与调用

47 Case 批次：Candidate 68 次、121,752 Token、估算 `0.152516 CNY`；评测 Judge 83 次、157,179 Token、估算 `0.684597 CNY`；校准 Judge 13 次、19,834 Token、估算 `0.089046 CNY`；Embedding 12 次、310 Token、估算 `0.000216 CNY`。整批总计 176 次，估算 `0.926375 CNY`，耗时 `884253 ms`，未超时或耗尽预算。上述是本地 rate-card 估算，不是账户账单。

v1 中断前的 10 个完成项因旧 runner 未逐项落丰富报告，其 Token 与费用无法可靠追溯，故不估算。v2 首个完整检查点记录文本 Provider 物理请求 `214` 次、Prompt `669,411` Token、Completion `208,907` Token，Embedding `17` 次 / `5,127` Token，估算总费用 `1.157301 CNY`。v3 的 10 个完整检查点合计文本物理请求 `2,427` 次、Prompt `6,805,554` Token、Completion `2,145,259` Token，Embedding `195` 次 / `53,670` Token，估算总费用 `11.843834 CNY`。v4 的 12 个完整检查点合计文本物理请求 `2,857` 次、Prompt `8,906,811` Token、Completion `2,837,043` Token，Embedding `249` 次 / `67,587` Token，估算总费用 `15.604751 CNY`；被中止 attempt 没有完整用量，因此不伪造。资源控制按用户最新授权采用“任意滚动 5 小时不超过 2000 AFP 积分”，不设金额硬上限；调用保持单批串行，未收到 AFP 限额告警。

本 Goal 的 Judge v2：兼容性 `1` 次请求 / `1,498` Token，校准 `13` 次请求 / `19,602` Token，合计 `14` 次 / `21,100` Token；Provider 未提供可核算的 Judge rate-card，因此费用明确记为不可得。Candidate/Embedding 没有参与 Judge 校准，47 Case Judge-only 未启动。

v5 recovery 健康门为 Candidate `6` 次 / `5,699` Token、Embedding `1` 次 / `57` Token；正式矩阵为文本 `3,619` 次 / `14,483,789` Token、Embedding `301` 次 / `84,118` Token，估算 `19.076491 CNY`。执行全程串行，未出现 401/403、404、429、余额不足或 AFP 限额告警；账户的 AFP 使用只能由 Provider 控制台确认，项目不把无告警伪写为精确积分消耗。

## 7. 总门禁

| 门禁 | 结果 | 证据 |
|---|---|---|
| backend pytest | 通过（离线） | `266 passed, 11 skipped`；11 项均因本轮未设置 `QINGHUAI_TEST_DATABASE_URL` 跳过 |
| Ruff | 通过 | 从 `core/backend` 使用项目配置检查 `app scripts migrations ../../test/backend`：`All checks passed` |
| mypy | 通过 | `Success: no issues found in 79 source files` |
| FastAPI app import | 通过 | 无 Key 导入输出 `Qinghuai Chat Backend` |
| 空库 Alembic upgrade/check | 通过 | `No new upgrade operations detected` |
| persistence/semantic PostgreSQL tests，关键项不 skip | 通过 | 专用库 `12 passed` |
| frontend lint/typecheck/Vitest/build | 通过 | Vitest `52/52`；production build 与 bundle-size gate 通过 |
| 现有 Playwright | 通过 | `11/11` |
| full-stack Playwright | 历史通过，本 Goal 未重跑 | 当前环境没有专用 PostgreSQL URL；保留既有 `1/1` 真实 REST/WS/PostgreSQL 证据，不伪报本轮通过 |
| CI 无真实 Ark Key/网络 | 通过（本地门禁 + 配置审计） | workflow 仅显式置空 Key，ArkClient 在缺 Key 时拒绝发请求；不覆盖默认 model/base URL，避免污染默认值单测；模型仅测试 app DI；pytest/Ruff/mypy 版本已固定；四个 job 可解析 |
| manifest 15/15 全分母完整性 | 通过 | recovery canonical 为 `15/15/15/14`、coverage `1.0`、`complete=true`；15 个 attempt/Run ID 唯一，未替换 seed、未遗漏 partial；三条路线均达到 manifest 聚合阈值 |
| README 与 canonical 指标一致 | 通过 | README 与 recovery canonical 均报告 `15/15/15/14`；同时保留旧 Provider-unavailable `15/0/0/0` 作为历史 execution，不相互覆盖 |
| `git diff --check`、凭据和绝对路径扫描 | 通过，最终提交前再核对 | canonical 无绝对路径或真实凭据；README 只含显式密码占位符 |
| 工作树干净 | 最终提交后复核 | 本 Goal 路径精确暂存；用户原有未跟踪 Goal 文档保持在工作树且不纳入本提交 |

## 8. 自动化不能替代的证据

- 两名真实人工的相互独立标注、分歧记录与仲裁；
- 真实玩家自然试玩的发现率、理解成本和主观体验。

仓库提供空白协议和模板，但在收到真实参与者输入前不会伪造完成状态，也不会让自动化 Agent 充当玩家样本。
