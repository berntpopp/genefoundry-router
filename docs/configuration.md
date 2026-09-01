# Configuration

Structure lives in committed [`servers.yaml`](../servers.yaml); URLs and secrets live in a
gitignored `.env` (copy [`.env.example`](../.env.example)). Every variable is prefixed `GF_`.

A backend with an unset URL or `enabled: false` is skipped with a warning; the router still
starts.

> **No token passthrough.** The caller is authenticated at the edge and their token is
> never forwarded to a backend (confused-deputy defence).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GF_HOST` / `GF_PORT` | `127.0.0.1` / `8000` | Bind address |
| `GF_MCP_PATH` | `/mcp` | MCP mount path |
| `GF_SERVERS_FILE` | `servers.yaml` | Backend registry |
| `GF_AUTH_MODE` | `none` | `none` \| `jwt` \| `oauth` (use jwt/oauth in production) |
| `GF_DEPLOYMENT_MODE` | `development` | `development` \| `production`; explicit reachability policy, because a loopback listener can still be published by a reverse proxy |
| `GF_ALLOW_INSECURE` | `false` | Opt-in to serve `auth=none` on a non-loopback bind (PoC only; it never weakens production observability controls) |
| `GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY` | `false` | Explicit, warning-emitting acknowledgement required for an authenticated development router without the production controls; valid only on loopback and rejected in production/non-loopback use |
| `GF_PUBLIC_BASE_URL` | _(unset)_ | Router's canonical public URL — OAuth resource URI + Protected-Resource-Metadata |
| `GF_ALLOWED_HOSTS` | _(empty)_ | CSV Host allowlist; required for every non-loopback bind |
| `GF_ALLOWED_ORIGINS` | _(empty)_ | CSV browser `Origin` allowlist. Rejects any other present `Origin` with 403 (DNS-rebinding defence) **and** serves CORS for `/mcp` — the listed origins are reflected in `Access-Control-Allow-Origin` and may preflight. Empty = reject every present `Origin` and grant no CORS. |
| `GF_JWT_ISSUER` | _(unset)_ | jwt/oauth: token issuer URL |
| `GF_JWT_JWKS_URL` | _(unset)_ | jwt/oauth: issuer JWKS endpoint (signature verification keys) |
| `GF_JWT_AUDIENCE` | _(unset)_ | jwt/oauth: required token `aud` (MUST match; audience binding) |
| `GF_OAUTH_CLIENT_ID` / `GF_OAUTH_CLIENT_SECRET` | _(unset)_ | oauth: upstream provider client credentials |
| `GF_OAUTH_AUTHORIZE_URL` / `GF_OAUTH_TOKEN_URL` | _(unset)_ | oauth: upstream provider authorize/token endpoints |
| `GF_OAUTH_JWT_SIGNING_KEY` | _(unset)_ | oauth: optional stable router signing, client-store encryption, and observability HMAC key; when unset, the legacy deterministic key derived from `GF_OAUTH_CLIENT_SECRET` is retained so existing DCR state remains readable |
| `GF_OAUTH_CANONICAL_ISSUER` | `https://genefoundry.org` | Canonical issuer for router-issued access and refresh tokens |
| `GF_OAUTH_LEGACY_ISSUERS` | `https://genefoundry.org/` | Exact temporary trailing-slash issuer alias; empty disables compatibility and any other value is rejected |
| `GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL` | `2026-09-06T00:00:00Z` | Absolute end of the 30-day legacy-issuer transition; it does not slide with process restarts |
| `GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS` | `43200` | oauth: router-issued reference-token lifetime in seconds (5 min–24 h); it does not lengthen the upstream IdP bearer token |
| `GF_REFRESH_OBSERVABILITY_DB` | _(unset)_ | oauth: durable refresh-rotation SQLite ledger. Production Compose sets `/data/genefoundry/refresh-observability.sqlite3` on the existing data volume |
| `GF_RATE_LIMIT_RPM` | `0` | Per-client requests/min (429 over). An authenticated `GF_DEPLOYMENT_MODE=production` router **refuses to start** with `0`, even on loopback behind a proxy |
| `GF_METRICS_TOKEN` | _(unset)_ | Bearer token for `GET /metrics`. An authenticated production router **refuses to start** without it, even on loopback behind a proxy |
| `GF_DRIFT_MODE` | `warn` | Runtime catalog policy: `off` \| `warn` \| `enforce` |
| `GF_DRIFT_BASELINE` | _(packaged)_ | Optional path override for the reviewed packaged baseline |
| `GF_<NAME>_URL` | _(unset)_ | Per-backend `/mcp` URL (e.g. `GF_GNOMAD_URL`) |

## Authentication

The router is a **resource server**: it *validates* tokens against an identity provider, it
does not mint them. An IdP (e.g. self-hosted Keycloak) is therefore required for both
authenticated modes.

- **`oauth`** — OAuth 2.1 for interactive/browser MCP clients (claude.ai, Cursor). The
  router serves Protected-Resource-Metadata (RFC 9728) + `WWW-Authenticate`, and proxies an
  upstream provider so clients can complete the login flow; access tokens are verified
  against `GF_JWT_JWKS_URL` with audience binding (`GF_JWT_AUDIENCE`). The router's
  OAuthProxy **is** the Dynamic-Client-Registration facade — it serves `/register` itself,
  so Keycloak's DCR stays closed; on the Keycloak client, whitelist the router's callback
  `https://genefoundry.org/auth/callback` as a Valid Redirect URI. The connector receives a
  router-issued reference token (12 hours by default); FastMCP continues to validate and,
  when necessary, refresh the short-lived upstream token. It must therefore be paired with
  a bounded upstream online session rather than an unrestricted offline token.
- **`jwt`** — machine-to-machine: verify bearer JWTs from `GF_JWT_ISSUER` (JWKS +
  audience), with no interactive-login facade.

### Startup guards

The router refuses to start `auth=none` on a non-loopback bind unless
`GF_ALLOW_INSECURE=true` (the explicit, logged escape hatch for a deliberately-public PoC).
It likewise refuses to start an authenticated `GF_DEPLOYMENT_MODE=production` router that
has no positive `GF_RATE_LIMIT_RPM`, or that would serve `GET /metrics` without
`GF_METRICS_TOKEN` — including a loopback listener published by a reverse proxy.

`GF_ALLOW_INSECURE` only controls the unauthenticated public-bind guard; it cannot weaken
production observability controls. A local authenticated development router may set the
separately named `GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY=true`; this emits a warning and
is accepted only for an authenticated loopback development process. Production
configuration and non-loopback use are rejected.

### Example — Keycloak at `auth.example.org`, realm `genefoundry`

```bash
GF_AUTH_MODE=oauth
GF_OAUTH_CLIENT_ID=genefoundry-router
GF_OAUTH_CLIENT_SECRET=…                 # secret; set in the server env, never commit
GF_OAUTH_AUTHORIZE_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/auth
GF_OAUTH_TOKEN_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/token
# Leave GF_OAUTH_JWT_SIGNING_KEY unset when upgrading an existing DCR store. A new
# explicit value intentionally starts a new signing/store identity and must then stay stable.
GF_OAUTH_CANONICAL_ISSUER=https://genefoundry.org
GF_OAUTH_LEGACY_ISSUERS=https://genefoundry.org/
GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL=2026-09-06T00:00:00Z
GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS=43200  # router reference token; 12 h default, 24 h maximum
GF_REFRESH_OBSERVABILITY_DB=/data/genefoundry/refresh-observability.sqlite3
GF_JWT_ISSUER=https://auth.example.org/realms/genefoundry
GF_JWT_JWKS_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/certs
GF_JWT_AUDIENCE=https://genefoundry.org/mcp   # Keycloak must stamp this into the token `aud`
GF_PUBLIC_BASE_URL=https://genefoundry.org    # ROOT origin, NO path — OAuth routes live at root
```

`GF_PUBLIC_BASE_URL` is the bare public origin: the OAuth endpoints (`/authorize`, `/token`,
`/register`, `/.well-known/*`) are served at the root, and the MCP endpoint is
`GF_PUBLIC_BASE_URL` + `GF_MCP_PATH` (→ `https://genefoundry.org/mcp`), which is also the
OAuth resource / token audience. Putting a path here (`…/mcp`) mis-advertises the OAuth
endpoints as `…/mcp/authorize` and doubles the protected-resource-metadata URL.

The issuer transition is deliberately narrow: new router tokens always use the canonical
issuer without a trailing slash; only the exact historical root-slash alias is accepted,
and only before `2026-09-06T00:00:00Z`. Remove the legacy alias after that deadline once
live sessions have drained. Keep the effective signing identity stable throughout the
transition: leave `GF_OAUTH_JWT_SIGNING_KEY` unset for a legacy store, or keep an already
configured value unchanged. Setting or changing it invalidates live router tokens and
selects a different encrypted Dynamic Client Registration store independently of issuer
compatibility.

**Verify:** an unauthenticated `POST /mcp` returns `401` + `WWW-Authenticate`; a request
bearing a valid issuer-signed, correctly-audienced token returns `200`. See
`.env.docker.example` for all three modes.
