# Agent 语义整改 Before / After

- 历史 before：`project/evaluation-results/live-baseline-2026-08-23`
- canonical after：`project/evaluation-results/live-remediation-2026-08-23`
- Candidate：`doubao-seed-2.0-lite`，未更换
- Case：47 个原 ID 全部保留；5 个有证据的歧义 Case 升到 v2

## 完整可比运行

| 指标 | 历史 before | 完整 live after |
| --- | ---: | ---: |
| Case / rule observation | 47 / 81 | 47 / 81 |
| execution.complete | true | true |
| Case 直接通过 / 需复核 | 23 / 24 | 30 / 17 |
| hard failure | 30（37.037%） | 0（0%） |
| protocol Schema | 65.4321% | 100% |
| first-attempt Schema | 70.2128% | 100% |
| Case constraint | 旧报告未拆分 | 100% |
| query / retrieval / evidence scope | 旧报告混判 | 100% / 100% / 100% |
| owner / canary / internal literal leak | 0 / 0 / 0 | 0 / 0 / 0 |
| Memory 单次调用 | 100% | 100% |
| Memory Precision@K / Recall@K / MRR | 47.4359% / 100% / 92.3077% | 47.4359% / 100% / 92.3077% |
| direct-question rule | 0% | 50%（随后确认 1 个 Scorer 假阴性） |
| repetition rate | 旧报告口径 | 7.4074% |
| Candidate P50 / P95 | 历史报告口径 | 3284.148 / 10315.326 ms |
| Goal progress Judge mean | 2.7073 | 3.1316 |
| Player agency Judge mean | 2.9024 | 3.9634 |
| Judge calibration | 2/13 | 1/13（7.6923%） |
| Judge Injection | 2/3 | 2/3 |
| 实际费用 | 历史基线费用见原报告 | ¥0.913314 |

after 的 68 次 Candidate、95 次 Judge（含 13 次校准）和 12 次 Embedding 全部完成，无错误、无超时、无预算耗尽。报告 SHA-256 为 `95fd60e12ce2d6ff12dd719fc3f5ffa854067bab2ba3123d140ea79cd6c478e6`。

## 单变量修订与恢复证据

完整 after 中 `relevance_001` 的两个回答分别是“开着。”和“今日照常营业。”。规则只接受后者，但 Judge 对前者判已回答、对后者反而判未回答，证明 50% 主要是关键词 Scorer 假阴性，而 Judge 也不稳定。

因此只修改一个 Case 变量：`relevance_001` 升到 v2，把“开着”加入合法肯定同义词。完整子集复评结果：

- 1 Case、2 Candidate observation、3 Judge calls；
- `execution.complete=true`，direct-question Rule=100%，Judge direct answer=100%；
- hard failure=0，Bad Case=0；
- 费用 ¥0.027722；报告 SHA-256 `1f210d98387996410252f61c2f7bc217534a03d762044712c9cafa18936c07e0`。

随后一次全量 v2 运行遭遇 Provider unavailable/timeout，并因重试把 Judge 100 次硬门用尽：`execution.complete=false`，不能作为 canonical。它仍证明 direct-question Rule=100%，同时暴露 2 个 `candidate_protocol_fallback` 和 1 个真实 query scope 违规。针对该 query scope，只给 ChatDecision 增加“memoryQuery actor/goal 只能来自候选列表”这一条 Prompt 约束；再对 3 个失败 Case 做 Candidate-only 恢复：3 Case、6 observation 全部通过，hard failure=0、query scope=100%、费用 ¥0.011407，SHA-256 `4588de449b3d594058a436d858cc3f3ae6e6af3181f630d5d0fedd585178e71e`。

上述恢复不是另一份完整 47 Case baseline，不能与 canonical 拼接成伪造的单次全绿运行。它只证明已识别的失败样本在最小修复/Provider 恢复后关闭。因 ¥2 总预算已接近上限，没有进行第三次完整全量复评。

## Judge 与检索结论

- Rubric v2 已按协议拆分，但 live 校准只有 1/13、Injection 2/3，Judge 必须继续标为 `advisory`，17 个语义 Bad Case 保留人工复核。
- PostgreSQL + pgvector owner-safe 集成已在专用测试库通过；这证明查询链与 owner 隔离，不等于线上 Embedding 的 Precision@K 达到 0.75。
- live fixture Memory Precision@K 仍为 0.474359，未达到 0.75 目标；Recall@K=1.0、owner 越界=0。
- `systemBlocked` 在 Candidate-only observation 中保持 `null`；不能从 Rule failure 反推后端阻断。后端权威安全由 RunService/Pydantic/owner-safe Tool 的全量测试证明。

## 总费用

本轮所有真实运行合计 ¥1.878824，低于用户授权的 ¥2：

- 完整 canonical after：¥0.913314；
- 首次 direct-question 子集（Judge 上限过紧，非完整）：¥0.019090；
- 完整 direct-question v2 子集：¥0.027722；
- 全量 v2 Provider/预算 partial：¥0.907291；
- 3 Case Candidate 恢复：¥0.011407。

中间 experiment/partial/probe 原始目录不进入提交；提交只保留历史 baseline、完整 canonical after、canonical offline 及必要验收文档。
