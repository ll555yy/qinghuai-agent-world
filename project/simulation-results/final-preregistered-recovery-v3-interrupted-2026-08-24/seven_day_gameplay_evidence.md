# Preregistered seven-day simulation evidence

- Experiment: `final-agent-validation-recovery-v3-20260824`
- Manifest digest: `3efe93e36137510c8ed6d944a593135fea106e0e72eafed83c3d7a665d6c4f40`
- Complete: `False`
- Planned / attempted / infra-valid / gameplay-pass: `15/11/10/0`
- Coverage / ITT / valid-run success: `0.733333` / `0.0` / `0.0`
- Requirement failures: `missing_report:final-agent-validation-recovery-v3-20260824:pro_zhao:20260850,missing_report:final-agent-validation-recovery-v3-20260824:pro_zhao:20260851,missing_report:final-agent-validation-recovery-v3-20260824:pro_zhao:20260852,missing_report:final-agent-validation-recovery-v3-20260824:pro_zhao:20260853,missing_report:final-agent-validation-recovery-v3-20260824:pro_zhao:20260854,pro_lin:pro_lin_gameplay_pass_below_gate,pro_lin:pro_lin_player_completed_below_gate,pro_zhao:pro_zhao_failure_control_below_gate`

| Route | Planned | Attempted | Infra-valid | Gameplay pass | Completed player tasks | Coverage | ITT | Valid-run | Complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `observer` | 5 | 5 | 5 | 0 | 0 | 1.0 | 0.0 | 0.0 | `False` |
| `pro_lin` | 5 | 5 | 5 | 0 | 1 | 1.0 | 0.0 | 0.0 | `False` |
| `pro_zhao` | 5 | 1 | 0 | 0 | 0 | 0.2 | 0.0 | None | `False` |

| Attempt | Route | Seed | Status | Terminal | Attempted | Infra-valid | Gameplay pass | Player result | Failure reasons | Source |
|---|---|---:|---|---|---|---|---|---|---|---|
| `final-agent-validation-recovery-v3-20260824:observer:20260850` | `observer` | 20260850 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:observer:20260851` | `observer` | 20260851 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:observer:20260852` | `observer` | 20260852 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:observer:20260853` | `observer` | 20260853 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:observer:20260854` | `observer` | 20260854 | `completed` | `True` | `True` | `True` | `False` | `n/a` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_lin:20260850` | `pro_lin` | 20260850 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_lin:20260851` | `pro_lin` | 20260851 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_lin:20260852` | `pro_lin` | 20260852 | `completed` | `True` | `True` | `True` | `False` | `completed` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_lin:20260853` | `pro_lin` | 20260853 | `completed` | `True` | `True` | `True` | `False` | `failed` | `batch_incomplete,support_task_not_completed` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_lin:20260854` | `pro_lin` | 20260854 | `completed` | `True` | `True` | `True` | `False` | `partial` | `batch_incomplete` | `recovered-interrupted-batch:final-agent-validation-recovery-v3-20260824` |
| `final-agent-validation-recovery-v3-20260824:pro_zhao:20260850` | `pro_zhao` | 20260850 | `runner_failed` | `True` | `True` | `False` | `False` | `n/a` | `attempt:provider_outage_manual_safety_stop,missing_report` | `n/a` |
| `final-agent-validation-recovery-v3-20260824:pro_zhao:20260851` | `pro_zhao` | 20260851 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-v3-20260824:pro_zhao:20260852` | `pro_zhao` | 20260852 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-v3-20260824:pro_zhao:20260853` | `pro_zhao` | 20260853 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
| `final-agent-validation-recovery-v3-20260824:pro_zhao:20260854` | `pro_zhao` | 20260854 | `not_started` | `True` | `False` | `False` | `False` | `n/a` | `attempt:provider_outage_batch_stopped,missing_report` | `n/a` |
