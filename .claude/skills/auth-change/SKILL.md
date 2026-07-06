---
name: auth-change
description: Use when changing the router's authentication or authorization — JWT/OAuth modes, verifiers, resource/audience metadata, the secure-by-default bind guard, or the no-token-passthrough boundary.
---

# Auth Change

Follow `AGENTS.md` first. Auth is the router's trust boundary; ground in docs/SECURITY-ASSESSMENT-2026-06-29.md and the MCP authorization spec.

## Workflow

1. Work in `auth.py` (verifiers/modes), `config.py` (`GF_AUTH_MODE`, `GF_ALLOW_INSECURE`, JWT/OAuth settings), and `cli.py` (bind guard) as applicable. Modes: `none` (dev / loopback), `jwt`, `oauth`.
2. **Never forward the caller's token** to backends — the enforcement is `make_proxy_client()` in `composition.py` (`forward_incoming_headers=False`); keep `tests/unit/test_no_token_passthrough.py` green.
3. Keep the **secure-by-default guard**: `is_insecure_public_bind` (in `cli.py`) refuses `GF_AUTH_MODE=none` on a non-loopback bind unless `GF_ALLOW_INSECURE=true`.
4. Audience binding (RFC 8707): `resource_base_url` = the audience (`…/mcp`), not the root `base_url`; tolerate a duplicated `/mcp` in the resource (some clients append it). Serve RFC 9728 Protected Resource Metadata + `WWW-Authenticate`.
5. For the OAuth proxy, keep a durable client store and skip redundant consent for an already-approved client.
6. Add / adjust unit tests; run `make ci-local`.

## Common mistakes

- Setting `resource_base_url` to the root `base_url` (breaks RFC 8707 audience validation).
- Relying on the framework default for header forwarding (it forwards the caller token).
