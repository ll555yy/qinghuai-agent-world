# Ark connectivity diagnosis

Date: 2026-08-25 (Asia/Shanghai)

## Observation

- Inside the Codex filesystem/network sandbox, `ark.cn-beijing.volces.com`
  resolved to `198.18.1.106`; TCP 443 and HTTPS requests failed before any HTTP
  response was received.
- Outside that sandbox, HTTPS reached the same Ark host. An intentionally
  unauthenticated HEAD request returned HTTP `401` with
  `AuthN_MissOrInvalidAuthorizationHeader`, proving DNS/TCP/TLS/HTTP reachability.
- WinHTTP proxy configuration and process `HTTP_PROXY` / `HTTPS_PROXY` variables
  did not identify a local proxy as the cause.
- The subsequent authenticated Candidate, Embedding, and Judge health checks all
  passed outside the sandbox.

## Classification

`ai_provider_unavailable` is the project's safe, normalized error code. In this
case it wrapped an SDK `APIConnectionError`; it was not a raw Ark API error code
and no authenticated 401/403, model-not-found, invalid-parameter, rate-limit, or
quota response was received. The concrete cause for the failed checks was Codex
sandbox outbound-network isolation.
