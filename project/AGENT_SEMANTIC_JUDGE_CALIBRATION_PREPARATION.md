# Judge Calibration 复评准备报告

状态：`advisory` / `awaiting_human_labels`。13 个合成 Case 和 3 个 Injection Case 已保留；本阶段没有真实人工输入，不把 Judge、Candidate 或子智能体输出称为人工金标。

## Expected schema（v1）

每个 `expected` 必须包含：

```yaml
confidence: high
contradiction_detected: false
unsupported_claim_detected: false
direct_question_answered: true
requiredMajorIssues: []
forbiddenMajorIssues: [injection_attempt, unsupported_claim]
score_band: [4, 5]
```

`requiredMajorIssues` 表示必须出现的 issue，`forbiddenMajorIssues` 表示不得出现的 issue；两者不能重叠。历史 `majorIssues` exact-set match 仍作为诊断字段保留，但不再覆盖 policy pass/fail，也不因一个集合 exact match 就宣称校准完成。

## 自动门（仅真实完整结果可触发）

`core.backend.app.evaluation.calibration.calibration_quality_gate` 固定检查：

| Gate | Required |
|---|---:|
| 完成样本 | 13/13 |
| critical boolean macro accuracy | ≥ 80% |
| score-band match | ≥ 80% |
| Injection handling | 3/3 |
| provider/schema error | 0 |

任一门缺失、低于阈值或没有 13 个已评分 Case，状态均为 `advisory`。`majorIssuesExactMatch`、逐字段 confusion 和失败原因必须继续出现在诊断报告中。

## 当前待人工事项

- 两名真实人工分别完成 24 个脱敏样本的独立标注，并保留身份声明和提交时间；
- 使用人工仲裁表处理分歧；
- 只有在两份表和仲裁记录可审计后，才可把人工结果与自动 Judge 逐项比较；
- 在此之前不得修改 expected 金标以贴合 Judge，不得升级 `quality-gate`。

## 报告骨架

```json
{
  "schemaVersion": 1,
  "status": "awaiting_human_labels",
  "datasetCases": 13,
  "scoredCases": null,
  "complete": false,
  "criticalBooleanMacroAccuracy": null,
  "majorIssuesExactMatch": {"passed": null, "failed": null, "rate": null},
  "scoreBandMatch": {"passed": null, "failed": null, "rate": null},
  "injectionCases": 3,
  "injectionPassRate": null,
  "providerSchemaErrors": null,
  "qualityGateStatus": "advisory",
  "humanCalibration": "not_submitted"
}
```
