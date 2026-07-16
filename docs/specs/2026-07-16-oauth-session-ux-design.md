# OAuth Session UX Design

## Goal

Let a user who remains signed in to GeneFoundry use an MCP connector throughout a
normal workday without weakening bearer-token or refresh-token protections.

## Decision

The authentication stack has two independent lifetimes, each with a distinct
security role:

| Layer | Lifetime | Reason |
|---|---:|---|
| Keycloak access token | 15 minutes | Limits the value of a stolen upstream bearer token. |
| Router OAuthProxy access token | 12 hours | Avoids needless connector reauthorization while the router revalidates the upstream session on each request. |
| Keycloak online SSO idle session | 24 hours | Supports a normal active day; a refresh resets the idle clock. |
| Keycloak online SSO maximum | 30 days | Provides a hard reauthentication bound. |

The router exposes `GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS`, defaulting to 12 hours
and bounded to 5 minutes through 24 hours.  It is passed directly to FastMCP
`OAuthProxy.fastmcp_access_token_expiry_seconds`; it is not a Keycloak token
lifetime.  FastMCP continues to validate the Keycloak token and transparently
refresh it when appropriate, so a valid router token cannot outlive the active
upstream session.

The VPS Keycloak policy will retain `revokeRefreshToken=true` and
`refreshTokenMaxReuse=0`.  The router is the only holder of the upstream
confidential-client refresh token and is single-worker in production, making
strict rotation the appropriate replay defense.

## Explicit non-goals

- Do not enable `offline_access`. Offline tokens require explicit consent,
  independently bounded offline idle/max lifetimes, and a user-visible
  revocation story.
- Do not configure an upstream revocation endpoint in FastMCP. The router must
  first prove it can revoke the actual Keycloak refresh token rather than its
  own reference token.
- Do not forward caller authorization headers to backend MCP servers.
- Do not alter transport: the public endpoint remains Streamable HTTP only.

## Rollout and rollback

Release and deploy the router change first, with
`GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS=43200` in the server-only router
environment.  Then apply the idempotent Keycloak realm patch and verify it with
the existing remote administration command.  Existing router tokens retain
their original expiry; users get the new lifetime on their next authorization.

To roll back the router behavior, set the environment value to `900` and
redeploy.  To roll back Keycloak, use the hardened-realm script's server-side
backup/revert facility; it never exports secrets from the VPS.
