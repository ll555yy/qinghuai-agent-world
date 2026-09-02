# Qinghuai P0 Benchmark

该目录包含本轮已完成且可独立运行的三条评测线：

- `business/`（P0-1）：12 个业务任务，比较 noop、随机合法、短视规则和完整 Agent。
- `memory/`（P0-2）：100 条 owner-scoped 查询，比较 hybrid、keyword、vector 和 no-graph。
- `reliability/`（P0-4）：8 类确定性故障，与同 seed 无故障控制比较状态 digest。

`common/` 提供统一 Manifest、SHA256、JSONL artifact、断点续跑、paired bootstrap 和 AFP 预算保护；`integrations/` 连接 Ark、PostgreSQL 与生产 `RunService`。

## 快速验证

```powershell
python -m benchmark.cli validate
python -m benchmark.cli pilot --suite all
python -m pytest -q benchmark/tests
```

`validate` 固定检查 12 个业务任务、100 条 Memory 查询（30 tuning / 70 holdout）和 8 × 10 次故障注入。

## 正式执行

CLI 自动加载仓库根目录 `.env`。业务和 Memory live 实验需要真实 Ark 配置；Memory 和 Reliability 还需要与开发库隔离的 `QINGHUAI_TEST_DATABASE_URL`。

```powershell
python -m benchmark.cli run --suite business --live --manual-afp
python -m benchmark.cli run --suite memory --live --manual-afp
python -m benchmark.cli run --suite reliability --live
python -m benchmark.cli resume --experiment-id <id>
python -m benchmark.cli report --experiment-id <id>
```

不使用 `--manual-afp` 时，还需配置 `ARK_AFP_ACCESS_KEY_ID` 和 `ARK_AFP_SECRET_ACCESS_KEY`。预算守卫按单并发执行，五小时窗口保留 100 AFP；用量接口失效或达到余量时停止并写入可恢复状态。

## Artifact 契约

每个实验在 `benchmark/results/<experiment-id>/` 生成：

```text
manifest.json          # 模型、seed、digest、阈值和预算
manifest.sha256        # manifest 完整性
attempts.jsonl         # 可断点续跑的原始 attempt
per-case.jsonl         # case 级结果
aggregate.json         # 聚合指标与置信区间
failure-analysis.md    # 保留全部失败
resume-metrics.json    # 仅从 aggregate 自动生成
budget-ledger.jsonl    # AFP 查询和停跑记录
```

`results/` 是本地原始实验，不进入版本控制；只有人工审核的聚合摘要可以放入 `published/`。研究假设未达到阈值仍算评测完成，但必须输出 `hypothesisVerified: false`，不得追加有利 seed。

## 结果边界

- P0-1 成功只由冻结后端 Goal、授权、承诺、关系和章节状态判定。A0 使用真实 Ark，但业务任务运行在 Benchmark 环境，不冒充完整七日生产回放。
- P0-2 正式语义向量由真实 Embedding 一次生成并在消融间复用；R4 关闭 owner guard，只是安全负控制。
- P0-4 在生产 `RunService + PostgreSQL` 边界做确定性注入；Provider 事故单独统计，不能混入本地恢复率。
