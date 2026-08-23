# 当前 Agent 真实语义基线

- 日期：2026-08-23
- 状态：47 Case 的真实 Candidate、主 Judge、20% 重评、Embedding 和 13 Case Judge 校准均已完成
- Candidate：`doubao-seed-2.0-lite`（现有生产调用链）
- Judge：`doubao-seed-2.1-turbo`（独立 Ark Responses API，不进入生产路径）
- 主报告：`project/evaluation-results/live-baseline-2026-08-23/agent_semantic_evaluation.json`

`complete=true` 只表示评测数据完整，不表示当前模型质量通过。真实基线暴露了大量 Bad Case，且 Judge 自身校准通过率较低；面试或复盘时必须同时展示这些限制。

## 数据与调用

| 项目 | 结果 |
|---|---:|
| Case | 47（persona 6 / boundary 6 / memory 11 / rules 12 / relevance 6 / coherence 6） |
| Candidate | 68 次 |
| 主 Judge | 82 次（68 主评 + 14 重评） |
| Judge 校准 | 13 Case，14 次真实请求（1 次受统计重试） |
| Embedding | 12 次 |
| 实际统计总请求 | 177 |
| Candidate Token | 118,633 |
| Judge Token（主评 + 校准） | 161,074 |
| Embedding Token | 310 |
| Candidate P95 | 9,181.941 ms |
| Judge P95（主评） | 6,480.934 ms |
| 本次完整基线估算费用 | CNY 0.851436 |

主运行先以 CNY 0.84 为硬门，完成 9/13 校准后按设计保存 partial；随后只续跑缺少的 4 个校准，增量硬上限 CNY 0.034、实际估算 CNY 0.024381。最终聚合上限为 CNY 0.861055。加上此前所有探针的保守上界，本 Goal 真实调用累计估算不高于 CNY 0.989237，低于用户授权的 CNY 1；真实账单以火山方舟控制台为准。

## 确定性规则结果

| 指标 | 结果 |
|---|---:|
| Schema success / first attempt | 0.654321 / 0.702128 |
| Hard failure observations | 30 / 81（0.370370） |
| owner / canary / internal literal leak | 0 / 0 / 0 |
| unauthorized memory / owner-boundary violation | 22 / 22 |
| invalid action / ID / evidence blocked rate | 1.0 / 1.0 / 1.0 |
| Memory single-call pass rate | 1.0 |
| Memory Precision@K / Recall@K / MRR | 0.474359 / 1.0 / 0.923077 |
| vector / graph hits | 2 / 3 |
| empty retrieval rate | 0.076923 |
| direct-question rule pass rate | 0.0 |
| repetition rate | 0.098765 |

`ownerLeakCount=0` 代表报告中未观察到明确的跨 owner 私密字面泄露；22 个 owner-boundary/unauthorized-memory 硬失败说明 Candidate 仍生成了缺少可信 scope 的 Memory/ID 引用，不能把这项表述为“信息边界完全通过”。

## 独立 LLM Judge 结果

| 维度 | 均值 | 中位数 |
|---|---:|---:|
| personaConsistency | 3.548780 | 4 |
| contextFaithfulness | 3.134146 | 4 |
| responseRelevance | 3.036585 | 3 |
| naturalness | 3.085366 | 4 |
| goalProgress | 2.707317 | 3 |
| playerAgency | 2.902439 | 3 |

- direct answer rate：0.134146
- contradiction rate：0.024390
- unsupported claim rate：0.134146
- confidence：high 70 / medium 12 / low 0
- 14 组重复评分：维度一致率 1.0、布尔一致率 0.952381、平均绝对分差 0.011905、majorIssues 一致率 0.785714、分差大于 1 的组数 0
- Judge Schema failure：0；主评 provider retry：1

## Judge 校准与人工复核

- 校准 Case：13/13 已真实评分，校准完整。
- 校准通过：2/13（0.153846）。
- Judge Injection：2/3（0.666667）；Prompt 数据边界静态检查 13/13。
- Bad Case：24 个；人工仲裁队列：24 个。

校准低通过率说明当前 Judge 对预定义 expected 标签的对齐不足，尤其在 `majorIssues`、unsupported claim、direct question 和 injection review reason 上存在偏差。因此六维分数可作为当前基线信号，但不能当作无需人工抽检的绝对真值。校准失败已完整保存在报告中，没有为了让 Judge 看起来更好而改标签或调 Prompt。

## 下一阶段建议（本 Goal 未实施）

1. 先修 Candidate 协议/ID scope 的真实缺陷或评测投影错配，再讨论语义 Prompt；安全硬门不能被均分覆盖。
2. 分析直接提问、Goal 推进、玩家自主性和复读 Bad Case，建立按协议的最小修复实验。
3. 单独迭代 Judge 校准 Rubric/标签一致性，保留版本并用人工双标样本验证；不要用当前低校准通过率的 Judge 自动调生产 Prompt。
4. 在专用 PostgreSQL 测试库补跑 `DatabaseMemoryRetriever -> RuleScorer` owner-safe 集成；当前 Precision/Recall/MRR 仍是版本化 fixture 指标，不等价于线上检索质量。
