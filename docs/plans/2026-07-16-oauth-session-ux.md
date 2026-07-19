# OAuth Session UX Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

1. Add a bounded router setting for the FastMCP OAuthProxy access-token lifetime.
   Write settings and provider-wiring tests first; verify the default is twelve
   hours and the configured value reaches FastMCP.
2. Add the setting to the router configuration reference and production OAuth
   example, documenting that it is a reference-token lifetime rather than a
   Keycloak bearer-token lifetime.
3. Extend the VPS realm policy and first-boot realm import with the 15-minute /
   24-hour / 30-day lifetime split. Write the realm-policy test first and keep
   strict refresh rotation unchanged.
4. Document the server-only router environment value, live Keycloak apply/check
   commands, expected behavior, and rollback in the VPS authentication guide.
5. Run focused tests, formatting/linting, then each repository's full local CI.
   Apply the live Keycloak policy through its existing backup-and-verify script;
   deploy the router only after its release is available to the VPS pin workflow.
