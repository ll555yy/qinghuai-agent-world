# v5 recovery health result

- Local date: `2026-08-25`
- Candidate: `0/6` protocols passed
- Candidate physical requests / provider retries: `12 / 6`
- Embedding: `0/2` public texts passed
- Embedding dimensions: expected `2048`, actual unavailable
- Embedding physical requests: `1`
- Returned token usage: unavailable for all failed requests
- Health gate: `failed`
- Start v5 recovery: `false`
- `humanValidated`: `false`

Every Candidate protocol returned `ai_provider_unavailable`; the Embedding
batch returned `embedding_provider_error / APIConnectionError`. No provider
request ID or token usage was returned. Under the preregistered stop rule this
single health round was not repeated, the manifest digests were not rechecked,
and no new execution ID, ledger, PostgreSQL run, or formal v5 attempt was
created. The old terminal v5 ledger remains untouched at `15 planned / 0
attempted`.
