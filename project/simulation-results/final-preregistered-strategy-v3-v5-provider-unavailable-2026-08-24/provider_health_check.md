# v5 Provider health check

- Performed at: `2026-08-24T21:37:12.0770125+08:00`
- Candidate: `0/6`; six physical requests, all `ai_provider_unavailable`, no format retry or token usage
- Embedding: one physical request for two fixed public inputs; `embedding_provider_error` / `APIConnectionError`, no token usage
- Formal v5 matrix: `15 planned / 0 attempted / 15 terminal not_started`
- Batch decision: do not poll again and do not start the matrix while both provider paths are unavailable
- Estimated health-check cost: unavailable because the provider returned no token usage; it is not reported as zero

No project Prompt, NPC private state, credential, response body, or user conversation is included in this record.
