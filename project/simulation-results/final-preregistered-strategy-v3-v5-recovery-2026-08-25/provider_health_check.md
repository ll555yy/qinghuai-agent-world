# v5 recovery provider health check

- Status: `passed`
- Live requests sent: `true`
- Candidate: `doubao-seed-2.0-lite`, `6/6` protocols passed
- Candidate physical requests / retries: `6 / 0`
- Candidate token usage: `5699`
- Embedding: `doubao-embedding-vision-251215`, `2/2` vectors passed
- Embedding dimensions: `2048`
- Embedding requests / tokens: `1 / 57`
- Recovery gate: `passed`
- Human validated: `false`

The requests were executed outside the Codex network sandbox after an external
connectivity check reached Ark. This append-only result corrects the attribution
of the earlier `ai_provider_unavailable` observation: that observation was a
project-level mapping of a connection failure caused by sandbox egress isolation,
not an authenticated Ark Provider failure.
