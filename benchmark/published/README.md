# Published P0 benchmark summary

审核日期：2026-09-01。原始 Trace 保留在本地 `benchmark/results/`，不上传仓库；下表只发布冻结实验的聚合数据。

机器可读版本：[`p0-summary-20260901.json`](p0-summary-20260901.json)。

| Suite | Experiment | Denominator | Reviewed result |
|---|---|---:|---|
| P0-1 Business | `formal-business-manual-afp-clean-20260901` | 960 attempts | A0 100%，B1 50.83%，B2 100%；A0-B1 +49.17pp（95% CI 42.92–55.42），A0-B2 0pp，整体假设未验证 |
| P0-2 Memory/RAG | `formal-memory-manual-afp-clean-v5-20260901` | 500 observations | R0 Recall@5 95%，holdout Recall@5 98.57%，owner 越界 0；同义改写和 graph-only 消融均 +100pp |
| P0-4 Reliability | `formal-reliability-production-clean-v2-20260901` | 80 injections | 恢复 80/80，状态分歧 0，重复副作用 0；P50 533.67ms，P95 3321.40ms |

限制：P0-1 未胜过短视规则；P0-2 的 R0 Precision@5 为 19%、FPR 为 72.27%；P0-4 是本地确定性边界注入而非真实 Provider 事故。
