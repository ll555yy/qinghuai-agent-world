# 最终 47 Case 复评准备

状态：`prepared_not_run`。当前只冻结一次完整复评所需的命令、输入哈希和报告字段；不拼接历史 baseline、局部恢复或人工未完成结果，也不伪造 live Candidate/Judge/Embedding 数字。

## 离线检查命令

```powershell
E:\anaconda3\envs\qinghuai-chat\python.exe -m core.backend.scripts.run_agent_semantic_evaluation --offline --cases core/evaluation/agent_semantic_cases.yaml --skip-judge-calibration --output project/evaluation-results/offline-final-preparation
E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest -c core/backend/pyproject.toml -q test/backend/unit/test_evaluation_report.py test/backend/unit/test_evaluation_rule_scorer.py test/backend/unit/test_evaluation_runner.py
```

离线命令只能证明编排、脱敏和规则口径；它不能填入 live 结果或替代 PostgreSQL 留出集。

## 受控 live 命令（由主会话在授权环境执行）

```powershell
E:\anaconda3\envs\qinghuai-chat\python.exe -m core.backend.scripts.run_agent_semantic_evaluation --live --enable-judge --cases core/evaluation/agent_semantic_cases.yaml --output project/evaluation-results/live-final-<date>
```

执行前必须记录 Candidate/Judge/Embedding 模型、Case/Prompt/策略哈希、调用预算、重试、费用和 source；执行后只接受 `selectedCases=47`、`completedCases=47`、`execution.complete=true` 的单个 canonical 报告。Provider 不可用、超时、预算停止或真实人工缺失必须原样保留并标明，不得以局部结果“补绿”。

## 报告骨架

```json
{
  "status": "prepared_not_run",
  "caseCount": 47,
  "execution": {"selectedCases": 47, "completedCases": null, "complete": false},
  "candidate": {"firstAttemptSchema": null, "finalSchema": null, "hardFailure": null, "directQuestion": null},
  "retrieval": {"fixture": null, "postgres": null, "live_embedding": null},
  "judge": {"advisory": true, "calibration": null},
  "manualQueue": {"historicalItems": 17, "humanArbitration": "pending"},
  "p95LatencyMs": null,
  "tokens": null,
  "estimatedCostCny": null,
  "sourceIds": []
}
```

`retrieval` 三个 source 永远分开；`strictPrecisionAtK` 不能由 `precisionAtReturned` 或改 K 分母覆盖。人工队列、Bad Case、P95、Token、费用和 SHA-256 在真实完整运行后才可填写。
