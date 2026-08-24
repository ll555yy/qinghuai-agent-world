# Judge v2 technical compatibility result

- Local date: `2026-08-25`
- Primary model: `deepseek-v4-pro`
- API: Ark Agent Plan `/responses`
- Result: `provider_unavailable`
- Physical requests / provider retries: `1 / 0`
- Returned token usage: `unavailable`
- Temperature / thinking / store: `0 / disabled / false`
- Strict `JudgeScore` JSON Schema: enabled
- Prompt SHA-256: `1c461e4be9e9da8f0502fa8224f2f32202edb357d004ebe0ff28b2af96ad786c`
- Schema SHA-256: `a1b2c63e95c49bbad18c6125f60a5cf62bffb165a73a3b2c59d2b684f5673b4b`
- `humanValidated`: `false`

The failure was `ai_provider_unavailable`, not an explicit model, endpoint, or
schema incompatibility. The preregistered policy therefore prohibited the
Flash fallback. No Judge v2 profile was frozen, the 13-case calibration was
not executed, and the saved 47 Candidate summaries were not re-judged. Judge
v1 evidence remains unchanged and advisory.
