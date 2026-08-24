# 语义评测人工标注准备包

状态：`awaiting_two_real_humans`。这是准备包，不是人工结果；没有任何自动填入金标、agreement 或仲裁结论。

## 冻结范围

从当前 47 个脱敏 Case 中按 category 轮转冻结 24 个样本（每类 4 个），只保留 Case ID、category 和 protocol，不复制 Candidate、私有 Memory、Prompt、凭据或 Trace。样本清单 SHA-256：

`dfada8038c9021e594267113e93d1b5c56a2833c265f9b1061e53f1361939e00`

| Sample | Case | Category | Protocol |
|---|---|---|---|
| sample-001 | boundary_001_prompt_injection | boundary | speech_generation |
| sample-002 | boundary_002_owner_scope | boundary | speech_generation |
| sample-003 | boundary_003_internal_fields | boundary | speech_generation |
| sample-004 | boundary_004_medical_privacy | boundary | speech_generation |
| sample-005 | coherence_001_no_contradiction | coherence | speech_generation |
| sample-006 | coherence_002_continuation | coherence | speech_generation |
| sample-007 | coherence_003_no_repetition | coherence | speech_generation |
| sample-008 | coherence_004_participant_change | coherence | chat_decision |
| sample-009 | memory_001_keyword | memory | memory_retrieval |
| sample-010 | memory_002_actor_filter | memory | memory_retrieval |
| sample-011 | memory_003_goal_filter | memory | memory_retrieval |
| sample-012 | memory_004_topic_alias | memory | memory_retrieval |
| sample-013 | persona_001_lin_tone | persona | speech_generation |
| sample-014 | persona_002_xingyao_distance | persona | speech_generation |
| sample-015 | persona_003_zhaolei_sales | persona | speech_generation |
| sample-016 | persona_004_chen_direct | persona | speech_generation |
| sample-017 | relevance_001_direct_question | relevance | speech_generation |
| sample-018 | relevance_002_irrelevant_request | relevance | speech_generation |
| sample-019 | relevance_003_actor_goal | relevance | chat_decision |
| sample-020 | relevance_004_visible_evidence | relevance | chat_decision |
| sample-021 | rules_001_daily_seek_chat | rules | daily_action |
| sample-022 | rules_002_daily_wait_shape | rules | daily_action |
| sample-023 | rules_003_invitation | rules | invitation |
| sample-024 | rules_004_chat_action | rules | chat_decision |

## 独立填写与禁止代填

1. 标注者 A、B 分别使用 [`annotation-a-blank.md`](./agent-semantic-annotation-a-blank.md) 和 [`annotation-b-blank.md`](./agent-semantic-annotation-b-blank.md)，提交前不可查看另一张表。
2. 每个样本填写 critical boolean、score band、required/forbidden major issues、Injection 处理和简短证据；未知必须留空或明确“不确定”，不能猜测。
3. Candidate、Judge、子智能体、脚本、规则评分器和重复运行结果都不能充当人工；自动输出即使格式完整也不计入人工样本。
4. 两份表均有真实人工身份声明后，才可使用 [`arbitration-blank.md`](./agent-semantic-annotation-arbitration-blank.md) 记录分歧和最终人工裁决。缺少任一真实人工表时 Judge 继续 `advisory`。

程序化生成入口：`core.backend.app.evaluation.annotation.build_annotation_package`；其 `humanRequired=true`、`automatedLabelsAllowed=false` 约束不可被报告合并器覆盖。
