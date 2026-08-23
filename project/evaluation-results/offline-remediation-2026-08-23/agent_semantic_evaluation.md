# Agent semantic evaluation

- Mode: `offline`
- Complete: `True`
- Cases: `47/47`
- Candidate calls: `68`
- Judge calls: `68`
- Embedding calls: `12`
- Estimated cost CNY: `0.0`
- Budget exhausted: `False`
- Timed out: `False`

| Cases | Passed | Hard failures | Review required |
|---:|---:|---:|---:|
| 47 | 47 | 0 | 0 |

## Deterministic rules

- Schema success: `1.0`
- Owner/canary/internal leaks: `0/0/0`
- Memory Precision@K / Recall@K / MRR: `0.474359 / 1.0 / 0.923077`
- Direct-question pass / repetition: `0.0 / 0.419753`
- Candidate P50 / P95 ms: `0.037 / 0.066`

## LLM Judge

- Model / rubric: `doubao-seed-2.1-turbo / agent-semantic-rubric-v2`
- Scores / repeat pairs: `68 / 0`
- Dimension consistency / mean |Δ|: `None / None`
- Contradiction / unsupported claim / direct answer: `0.0 / 0.0 / 1.0`
- Schema failures / injection pass: `0 / None`
- Judge advisory: `True` (calibration_pass_rate_missing, injection_not_3_of_3)
- Format / provider / total retries: `0 / 0 / 0`

## Bad cases

None.
