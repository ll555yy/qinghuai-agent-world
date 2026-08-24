# PostgreSQL Memory 检索留出集准备报告

状态：`prepared_not_run`。本报告冻结口径和报告骨架，不伪造 PostgreSQL、Embedding 或真实网络结果。

## 证据边界

历史 Case 观测的 `retrievedMemoryIds` 来源是 `fixture`，只用于保留 strict Precision@K 基线；它不能与 `postgres` 或 `live_embedding` 结果求平均。实现中的 `CandidateObservation.retrievalSource` 和报告的 `retrievalMetricsBySource` 强制按来源分桶，单来源旧键 `memoryPrecisionAtK` 在混合报告中置空。

PostgreSQL benchmark 的每个样本必须实际执行：

```python
result = await retriever.search(
    run_id=sample.run_id,
    owner_npc_id=sample.owner_npc_id,
    query=sample.query,
)
```

其中 `retriever` 是 `DatabaseMemoryRetriever`；Case 预置的 expected ID 只参与评分，不能作为 returned ID。入口为 `core.backend.app.evaluation.retrieval_benchmark.run_postgres_retrieval_benchmark`。

## 数据拆分与阶段

样本在运行前固定为 `tuning` 与 `holdout` 两个不重叠 split。每个样本固定一个阶段：`vector`、`keyword`、`actor`、`goal`、`topic` 或 `graph`。报告同时输出 `splitMetrics` 和 `phaseMetrics`，并记录候选结果、融合/排序后的结果、Graph hit 数、owner 越界数和实际 search 调用数。

调参阶段只允许验证可解释的阈值、有限 Graph seed、分数归一化或 RRF；在没有留出证据前不增加 reranker，也不为凑满 K 返回弱相关 Memory。Graph seed 必须来自正向候选，不得因“任意正 vector score”扩大到所有近期 Memory。

## 预注册门槛

`RetrievalAcceptanceThresholds` 默认固定：holdout `precisionAtReturned >= 0.90`、`recallAtK >= 0.90`、`falsePositiveRate <= 0.10`、owner 越界 `=0`、重复结果 `=0`、空查询正确率 `=1`（若包含空查询），以及 MRR 不低于运行前声明的基线。strict Precision@K 仍单独保留，不改变分母。

无专用数据库 URL、Embedding 端口或可审计样本时，报告必须保持 `prepared_not_run`；禁止用 fixture 数字填入 holdout。

## 结果骨架

```json
{
  "schemaVersion": 1,
  "retrievalSource": "postgres",
  "status": "prepared_not_run",
  "tuning": {"vector": null, "keyword": null, "actor": null, "goal": null, "topic": null, "graph": null},
  "holdout": {"vector": null, "keyword": null, "actor": null, "goal": null, "topic": null, "graph": null},
  "acceptanceChecks": null,
  "retrieverSearchCalls": 0,
  "ownerBoundaryViolations": null,
  "sourceMixing": "forbidden"
}
```

这份骨架中的 `null` 是未运行的明确状态，不是零分或通过。
