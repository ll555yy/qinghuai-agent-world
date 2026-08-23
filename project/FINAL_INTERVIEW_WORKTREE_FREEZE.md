# Final interview readiness worktree freeze

- Freeze time: 2026-08-23 (Asia/Shanghai)
- Frozen HEAD: `50d6c88b0f30590e062b647da6dbb2d7fd74cc10`
- Rule: preserve every pre-existing change and raw experiment; do not reset,
  overwrite, or selectively delete evidence.

## File ownership at freeze

### Existing seven-day reachability work

- `core/backend/app/simulation/runner.py`
- `core/backend/app/simulation/evidence.py`
- `core/backend/scripts/run_seven_day_simulation.py`
- `core/backend/scripts/summarize_seven_day_evidence.py`
- `test/backend/unit/test_simulation_runner.py`
- `test/backend/unit/test_simulation_evidence.py`
- `project/PROMPT_GAMEPLAY_TUNING_LOG.md`
- `project/REAL_SEVEN_DAY_SIMULATION_RESULTS.md`
- `project/SEVEN_DAY_SIMULATION_GUIDE.md`
- ignored raw evidence below `simulation_reports/`

These files form the first baseline commit after their focused offline gates
pass. They must not be mixed with the new preregistered 15-run matrix.

### Canonical semantic evaluation evidence already committed

- `project/evaluation-results/live-baseline-2026-08-23/`
- `project/evaluation-results/live-remediation-2026-08-23/`
- `project/evaluation-results/offline-remediation-2026-08-23/`
- `core/evaluation/agent_semantic_cases.yaml`
- `core/evaluation/judge_calibration_cases.yaml`

### Local investigation artifacts

The exact `evaluation_reports/` and `project/evaluation-results/` directories
listed in `.gitignore` are preserved in place. They are probe, partial,
dry-run, recovery, or duplicate outputs and are not canonical. The existing
`simulation_reports/` tree remains locally ignored and preserved. No file is
deleted or overwritten by the archive policy.

### Final-stage additions

- experiment manifest and its validator/tests;
- retrieval benchmark and human-labelling package;
- preregistered 15-run full-denominator evidence;
- offline CI and real full-stack E2E;
- canonical final reports, README assets, and final acceptance report.

## Frozen SHA-256 evidence

| Artifact | SHA-256 |
|---|---|
| semantic Case source | `29354573692efe15f26addcb11d3f0e9f34e343907c9a6465eb8f95ccb445981` |
| Judge calibration Case source | `f3d9c672cbf0291793432d72bf6e43645bf4542459fd5bc0d53efe899df3d583` |
| historical live baseline JSON | `444067d97327356b5946598ad77715b29fac8dc7dd1d0e8cbb89900a8b5060a5` |
| remediation live canonical JSON | `95fd60e12ce2d6ff12dd719fc3f5ffa854067bab2ba3123d140ea79cd6c478e6` |
| remediation offline canonical JSON | `f642c34d2bc1760cdf5808c304d01d82ddf4771135922ff8fa3b0e01a13c9337` |
| local historical reachability aggregate JSON | `6eab054158e4331511ae1237ad7660759e72c788a85ba16290ac21a60b997bee` |
| seven-day runner at freeze | `8f86e6b2df1d4f4dffec6283162c11299304900315c854dd42608fc50d8bd677` |
| seven-day evidence aggregator at freeze | `6ebadfa9527e15199f2e3821e364ca590c0ad5ee814b37f3c470d7f85696ac42` |
| seven-day CLI at freeze | `b494610427048a27c601aefbc019149c0f42e80e568d9025af1bb6046ab48ea8` |
| evidence CLI at freeze | `79b1a870bce8dc79060106b8e4fe56e3315df7946484b266851102f774b469cd` |

The local historical aggregate is evidence from the previous selected-run
matrix, not evidence for the new preregistered batch. It contains absolute
source paths and therefore must not be promoted to the final canonical set.

## Canonical whitelist

At final acceptance, `project/evaluation-results/` may contain only:

- the historical live baseline;
- the complete remediation live run;
- the remediation offline run;
- the new single-run complete 47-Case final evaluation;
- the PostgreSQL retrieval holdout report;
- their minimal Markdown companions and external SHA-256 manifests.

The preregistered seven-day matrix is stored separately with source IDs or
repository-relative paths. Missing, failed, timed-out, or unstarted planned
attempts remain visible in its denominator.
