# Preregistered seven-day simulation evidence

- Experiment: `final-agent-validation-strategy-v2-20260824`
- Manifest digest: `ebc8d913f2f5f74366ce26f865451b796d1e98e53b07bc7fc076626445a4bbd5`
- Complete: `False`
- Planned / attempted / infra-valid / gameplay-pass: `15/13/12/0`
- Coverage / ITT / valid-run success: `0.866667` / `0.0` / `0.0`
- Requirement failures: `missing_report:final-agent-validation-strategy-v2-20260824:pro_zhao:20260857,missing_report:final-agent-validation-strategy-v2-20260824:pro_zhao:20260858,missing_report:final-agent-validation-strategy-v2-20260824:pro_zhao:20260859,pro_lin:pro_lin_gameplay_pass_below_gate,pro_lin:pro_lin_player_completed_below_gate,pro_zhao:pro_zhao_failure_control_below_gate`

| Route | Planned | Attempted | Infra-valid | Gameplay pass | Completed player tasks | Coverage | ITT | Valid-run | Complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `observer` | 5 | 5 | 5 | 0 | 0 | 1.0 | 0.0 | 0.0 | `False` |
| `pro_lin` | 5 | 5 | 5 | 0 | 0 | 1.0 | 0.0 | 0.0 | `False` |
| `pro_zhao` | 5 | 3 | 2 | 0 | 0 | 0.6 | 0.0 | 0.0 | `False` |

| Attempt | Route | Seed | Status | Terminal | Attempted | Infra-valid | Gameplay pass | Player result | Failure reasons | Source |
|---|---|---:|---|---|---|---|---|---|---|---|
| `final-agent-validation-strategy-v2-20260824:observer:20260855` | `observer` | 20260855 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:observer:20260855` |
| `final-agent-validation-strategy-v2-20260824:observer:20260856` | `observer` | 20260856 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:observer:20260856` |
| `final-agent-validation-strategy-v2-20260824:observer:20260857` | `observer` | 20260857 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:observer:20260857` |
| `final-agent-validation-strategy-v2-20260824:observer:20260858` | `observer` | 20260858 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:observer:20260858` |
| `final-agent-validation-strategy-v2-20260824:observer:20260859` | `observer` | 20260859 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:observer:20260859` |
| `final-agent-validation-strategy-v2-20260824:pro_lin:20260855` | `pro_lin` | 20260855 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_lin:20260855` |
| `final-agent-validation-strategy-v2-20260824:pro_lin:20260856` | `pro_lin` | 20260856 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_lin:20260856` |
| `final-agent-validation-strategy-v2-20260824:pro_lin:20260857` | `pro_lin` | 20260857 | `completed` | `True` | `True` | `True` | `False` | `failed` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,success_branch_not_reached,support_task_not_completed,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_lin:20260857` |
| `final-agent-validation-strategy-v2-20260824:pro_lin:20260858` | `pro_lin` | 20260858 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_lin:20260858` |
| `final-agent-validation-strategy-v2-20260824:pro_lin:20260859` | `pro_lin` | 20260859 | `completed` | `True` | `True` | `True` | `False` | `failed` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,success_branch_not_reached,support_task_not_completed,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_lin:20260859` |
| `final-agent-validation-strategy-v2-20260824:pro_zhao:20260855` | `pro_zhao` | 20260855 | `completed` | `True` | `True` | `True` | `False` | `failed` | `batch_incomplete,embedding_preflight_not_proven,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_zhao:20260855` |
| `final-agent-validation-strategy-v2-20260824:pro_zhao:20260856` | `pro_zhao` | 20260856 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete,embedding_preflight_not_proven,failure_control_changed_branch,failure_control_not_failed,not_postgres_backend,temporary_runs_kept` | `attempt-checkpoint:final-agent-validation-strategy-v2-20260824:pro_zhao:20260856` |
| `final-agent-validation-strategy-v2-20260824:pro_zhao:20260857` | `pro_zhao` | 20260857 | `runner_failed` | `True` | `True` | `False` | `False` | `n/a` | `attempt:provider_outage_storm_operator_interrupt,missing_report` | `n/a` |
| `final-agent-validation-strategy-v2-20260824:pro_zhao:20260858` | `pro_zhao` | 20260858 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:batch_stopped_after_provider_outage_storm,missing_report` | `n/a` |
| `final-agent-validation-strategy-v2-20260824:pro_zhao:20260859` | `pro_zhao` | 20260859 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:batch_stopped_after_provider_outage_storm,missing_report` | `n/a` |
