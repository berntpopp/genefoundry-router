---
name: security-review
description: Use when reviewing genefoundry-router security before deploy, when touching auth/proxy/logging/limits config, or when answering an infosec/DSB question.
---

# Security Review (router)

Follow `AGENTS.md` first. The router is the **trust boundary**: it owns edge auth and must never forward the caller's token to backends. Ground in docs/SECURITY-ASSESSMENT-2026-06-29.md, docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md (§Error-message sanitation), and the FastMCP not-found reflection guard.

## Checklist

1. **No token passthrough** — every proxy client goes through `make_proxy_client()` (in `composition.py`) with `forward_incoming_headers=False`; the caller's `Authorization` is never re-sent to a backend (fastmcp's `ProxyClient` default forwards it — regression-tested in `tests/unit/test_no_token_passthrough.py`).
2. **Secure-by-default** — the startup guard (`is_insecure_public_bind` in `cli.py`, flag `GF_ALLOW_INSECURE` in `config.py`) refuses `GF_AUTH_MODE=none` on a non-loopback bind unless `GF_ALLOW_INSECURE=true`.
3. **Auth wiring** — audience-bound `JWTVerifier`, RFC 9728 Protected Resource Metadata, `WWW-Authenticate`, OAuth proxy; audience = the `…/mcp` resource (RFC 8707), tolerant of a duplicated `/mcp`.
4. **Origin validation** on (DNS-rebinding MUST); **Streamable HTTP only**, no SSE.
5. **Inbound limits** — body-size cap (`GF_MAX_BODY_BYTES`) and opt-in rate limit (`GF_RATE_LIMIT_RPM`); outbound `GF_BACKEND_TIMEOUT`.
6. **PII-safe audit log** — tool / namespace / outcome / elapsed / correlation-id only; never args, results, or exception text.
7. **Backends not publicly reachable** — expose-only behind the proxy; verify their ports aren't on the public IP.
8. **Tool-definition drift** — `make fleet-probe` / `genefoundry-router drift` as a rug-pull / tool-poisoning tripwire; re-pin the baseline only after reviewing the diff.
9. **Error-message / identity sanitation** — never reflect a caller-supplied tool / resource / prompt name or URI (grammar-valid but possibly nonexistent) into caller-visible error text, the structlog audit sink, or Prometheus labels; registry-unresolved names bucket to `_unknown` (`safe_log_identity` in `observability.py`), and the AggregateProvider provider-fault log is scrubbed (`notfound_guard.py`). See Response-Envelope v1.1 §Error-message sanitation and the FastMCP not-found reflection guard.

## Common mistakes

- Trusting the "no passthrough" invariant without the `make_proxy_client` enforcement — a library default undid it once.
- Enabling `auth=none` on a public bind by overriding the guard.
