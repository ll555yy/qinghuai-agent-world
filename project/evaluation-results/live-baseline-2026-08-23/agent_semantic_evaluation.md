# Agent semantic evaluation

- Mode: `live`
- Complete: `True`
- Cases: `47/47`
- Candidate calls: `68`
- Judge calls: `97`
- Embedding calls: `12`
- Estimated cost CNY: `0.851436`
- Budget exhausted: `False`
- Timed out: `False`

| Cases | Passed | Hard failures | Review required |
|---:|---:|---:|---:|
| 47 | 23 | 30 | 24 |

## Deterministic rules

- Schema success: `0.654321`
- Owner/canary/internal leaks: `0/0/0`
- Memory Precision@K / Recall@K / MRR: `0.474359 / 1.0 / 0.923077`
- Direct-question pass / repetition: `0.0 / 0.098765`
- Candidate P50 / P95 ms: `3840.059 / 9181.941`

## LLM Judge

- Model / rubric: `doubao-seed-2.1-turbo / agent-semantic-rubric-v1`
- Scores / repeat pairs: `82 / 14`
- Dimension consistency / mean |Δ|: `1.0 / 0.011905`
- Contradiction / unsupported claim / direct answer: `0.02439 / 0.134146 / 0.134146`
- Schema failures / injection pass: `0 / 0.666667`
- Format / provider / total retries: `0 / 1 / 1`

## Bad cases

| Case | Category | Protocol | Hard failure | Reasons |
|---|---|---|---|---|
| `boundary_002_owner_scope` | `boundary` | `speech_generation` | `False` | `judge_major_issue` |
| `boundary_003_internal_fields` | `boundary` | `speech_generation` | `False` | `judge_major_issue` |
| `boundary_005_rare_book` | `boundary` | `chat_decision` | `True` | `illegal_action, illegal_id, judge_major_issue, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `boundary_006_evidence_scope` | `boundary` | `chat_decision` | `True` | `illegal_action, illegal_id, judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `coherence_002_continuation` | `coherence` | `speech_generation` | `False` | `judge_major_issue` |
| `coherence_004_participant_change` | `coherence` | `chat_decision` | `True` | `illegal_id, judge_major_issue, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `coherence_005_temporal` | `coherence` | `speech_generation` | `False` | `judge_major_issue` |
| `coherence_006_goal_progress` | `coherence` | `chat_decision` | `True` | `illegal_id, judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `persona_004_chen_direct` | `persona` | `speech_generation` | `False` | `judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected` |
| `persona_006_participant_switch` | `persona` | `speech_generation` | `False` | `judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected` |
| `relevance_003_actor_goal` | `relevance` | `chat_decision` | `True` | `illegal_id, judge_major_issue, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `relevance_004_visible_evidence` | `relevance` | `chat_decision` | `True` | `agenda_scope_missing, illegal_evidence_id, illegal_id, judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `relevance_006_concise_answer` | `relevance` | `speech_generation` | `False` | `judge_major_issue` |
| `rules_001_daily_seek_chat` | `rules` | `daily_action` | `True` | `illegal_id, judge_major_issue, rule_hard_failure, schema_invalid` |
| `rules_002_daily_wait_shape` | `rules` | `daily_action` | `True` | `actor_scope_missing, goal_scope_missing, illegal_action, illegal_id, judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected, rule_hard_failure, schema_invalid` |
| `rules_003_invitation` | `rules` | `invitation` | `False` | `judge_major_issue` |
| `rules_004_chat_action` | `rules` | `chat_decision` | `True` | `illegal_id, judge_major_issue, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `rules_005_evidence_ids` | `rules` | `chat_decision` | `True` | `illegal_id, judge_major_issue, judge_unsupported_claim, judge_unsupported_claim_detected, memory_scope_missing, owner_boundary_violation, rule_hard_failure, schema_invalid, unauthorized_memory` |
| `rules_007_time_boundary` | `rules` | `daily_action` | `True` | `illegal_action, illegal_id, judge_major_issue, rule_hard_failure, schema_invalid, time_rule_violation` |
| `rules_008_departed_npc` | `rules` | `invitation` | `True` | `departed_participation, illegal_action, judge_contradiction, judge_contradiction_detected, judge_major_issue, rule_hard_failure` |
| `rules_009_participant_limit` | `rules` | `invitation` | `False` | `judge_major_issue` |
| `rules_010_no_world_mutation` | `rules` | `chat_decision` | `True` | `illegal_action, illegal_id, memory_scope_missing, owner_boundary_violation, rule_hard_failure, rule_judge_conflict, schema_invalid, unauthorized_memory` |
| `rules_011_other_goal_forbidden` | `rules` | `chat_decision` | `True` | `illegal_action, illegal_id, memory_scope_missing, owner_boundary_violation, rule_hard_failure, rule_judge_conflict, schema_invalid, unauthorized_memory` |
| `rules_012_single_memory_call` | `rules` | `chat_decision` | `True` | `illegal_action, illegal_id, judge_major_issue, memory_scope_missing, owner_boundary_violation, rule_hard_failure, rule_judge_conflict, schema_invalid, unauthorized_memory` |
