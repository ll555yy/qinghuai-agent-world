# Preregistered seven-day simulation evidence

- Experiment: `final-agent-validation-recovery-20260823`
- Manifest digest: `0bc0a42bd71f1c98ea3229bea74db59021dfef028d43438e21cc07a662fcfcfe`
- Complete: `False`
- Planned / attempted / infra-valid / gameplay-pass: `15/2/1/0`
- Coverage / ITT / valid-run success: `0.133333` / `0.0` / `0.0`
- Requirement failures: `missing_report:final-agent-validation-recovery-20260823:observer:20260846,missing_report:final-agent-validation-recovery-20260823:observer:20260847,missing_report:final-agent-validation-recovery-20260823:observer:20260848,missing_report:final-agent-validation-recovery-20260823:observer:20260849,missing_report:final-agent-validation-recovery-20260823:pro_lin:20260845,missing_report:final-agent-validation-recovery-20260823:pro_lin:20260846,missing_report:final-agent-validation-recovery-20260823:pro_lin:20260847,missing_report:final-agent-validation-recovery-20260823:pro_lin:20260848,missing_report:final-agent-validation-recovery-20260823:pro_lin:20260849,missing_report:final-agent-validation-recovery-20260823:pro_zhao:20260845,missing_report:final-agent-validation-recovery-20260823:pro_zhao:20260846,missing_report:final-agent-validation-recovery-20260823:pro_zhao:20260847,missing_report:final-agent-validation-recovery-20260823:pro_zhao:20260848,missing_report:final-agent-validation-recovery-20260823:pro_zhao:20260849,pro_lin:pro_lin_gameplay_pass_below_gate,pro_lin:pro_lin_player_completed_below_gate,pro_zhao:pro_zhao_failure_control_below_gate`

| Route | Planned | Attempted | Infra-valid | Gameplay pass | Completed player tasks | Coverage | ITT | Valid-run | Complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `observer` | 5 | 2 | 1 | 0 | 0 | 0.4 | 0.0 | 0.0 | `False` |
| `pro_lin` | 5 | 0 | 0 | 0 | 0 | 0.0 | 0.0 | None | `False` |
| `pro_zhao` | 5 | 0 | 0 | 0 | 0 | 0.0 | 0.0 | None | `False` |

| Attempt | Route | Seed | Status | Terminal | Attempted | Infra-valid | Gameplay pass | Player result | Failure reasons | Source |
|---|---|---:|---|---|---|---|---|---|---|---|
| `final-agent-validation-recovery-20260823:observer:20260845` | `observer` | 20260845 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,repository_not_recovered,temporary_run_not_deleted,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-recovery-20260823:observer:20260845` |
| `final-agent-validation-recovery-20260823:observer:20260846` | `observer` | 20260846 | `runner_failed` | `True` | `True` | `False` | `False` | `n/a` | `attempt:repeated_provider_timeouts_safety_stop,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:observer:20260847` | `observer` | 20260847 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:observer:20260848` | `observer` | 20260848 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:observer:20260849` | `observer` | 20260849 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_lin:20260845` | `pro_lin` | 20260845 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_lin:20260846` | `pro_lin` | 20260846 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_lin:20260847` | `pro_lin` | 20260847 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_lin:20260848` | `pro_lin` | 20260848 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_lin:20260849` | `pro_lin` | 20260849 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_zhao:20260845` | `pro_zhao` | 20260845 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_zhao:20260846` | `pro_zhao` | 20260846 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_zhao:20260847` | `pro_zhao` | 20260847 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_zhao:20260848` | `pro_zhao` | 20260848 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-20260823:pro_zhao:20260849` | `pro_zhao` | 20260849 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
