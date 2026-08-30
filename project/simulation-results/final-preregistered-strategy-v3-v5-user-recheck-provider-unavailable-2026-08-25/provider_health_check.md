# v5 user-requested health recheck

- Local date: `2026-08-25`
- Candidate: `0/6` protocols passed
- Candidate physical requests / provider retries: `12 / 6`
- Embedding: `0/2` public texts passed
- Embedding dimensions: expected `2048`, actual unavailable
- Embedding physical requests: `1`
- Returned token usage: unavailable for all failed requests
- Health gate: `failed`
- Start v5 recovery: `false`
- User-authorized recheck: `true`
- `humanValidated`: `false`

The user explicitly requested a new health round after the previous terminal
result. Every Candidate protocol again returned `ai_provider_unavailable`; the
Embedding batch returned `embedding_provider_error / APIConnectionError`.
This append-only artifact does not replace the earlier health evidence. The
hard gate still prohibits manifest verification and the formal 15-attempt
matrix, so no new execution ID, ledger, PostgreSQL Run, or v6 was created.
