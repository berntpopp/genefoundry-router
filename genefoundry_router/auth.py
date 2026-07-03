"""Pluggable auth assembly for the router (GF_AUTH_MODE = none|jwt|oauth).

R1.6 invariant: the gateway authenticates the *caller* at this edge; it MUST NOT
forward the caller's token to the 13 backends (confused-deputy). Backend proxies use
the router's own connection (see composition.py). Never wire the incoming
``Authorization`` header into ``ProxyClient``.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import AnyHttpUrl

from genefoundry_router.config import RouterSettings
from genefoundry_router.exceptions import ConfigurationError

log = structlog.get_logger(__name__)

# GF_OAUTH_REQUIRE_CONSENT (string env) → OAuthProxy.require_authorization_consent
# (bool | Literal["remember", "external"]). "external" skips the built-in consent page.
_CONSENT_ARG: dict[str, bool | Literal["remember", "external"]] = {
    "external": "external",
    "remember": "remember",
    "true": True,
    "false": False,
}


def build_auth(settings: RouterSettings) -> Any | None:
    """Return a FastMCP auth provider for the configured mode, or None for 'none'."""
    mode = settings.GF_AUTH_MODE
    if mode == "none":
        log.info("auth_mode", mode="none")
        return None
    if mode == "jwt":
        return _build_jwt(settings)
    if mode == "oauth":
        return _build_oauth(settings)
    raise ConfigurationError(f"unknown GF_AUTH_MODE: {mode!r}")  # pragma: no cover


def _build_jwt_verifier(settings: RouterSettings) -> Any:
    """Build the raw audience-bound JWTVerifier (a TokenVerifier).

    Used directly as ``OAuthProxy.token_verifier`` and wrapped by RemoteAuthProvider in
    jwt mode. MCP auth (2025-11-25): audience binding is a MUST for a protected resource.
    """
    if not (settings.GF_JWT_ISSUER and settings.GF_JWT_JWKS_URL and settings.GF_JWT_AUDIENCE):
        raise ConfigurationError(
            "jwt mode requires GF_JWT_ISSUER, GF_JWT_JWKS_URL, and GF_JWT_AUDIENCE (audience MUST)"
        )
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    return JWTVerifier(
        jwks_uri=settings.GF_JWT_JWKS_URL,
        issuer=settings.GF_JWT_ISSUER,
        audience=settings.GF_JWT_AUDIENCE,  # reject tokens not minted for this server
        base_url=settings.GF_PUBLIC_BASE_URL,  # canonical public resource URI (PRM)
    )


def _build_jwt(settings: RouterSettings) -> Any:
    """jwt mode: wrap the verifier in a RemoteAuthProvider so the router SERVES the MCP
    Protected-Resource-Metadata document (RFC 9728) + 401/WWW-Authenticate.

    Deviation from the plan, verified against fastmcp 3.4.2: a *bare* JWTVerifier
    validates tokens and emits WWW-Authenticate (with a resource_metadata pointer) but
    its ``get_well_known_routes()`` is empty — it does NOT serve a PRM document.
    RemoteAuthProvider serves a real PRM listing the issuer as the authorization server.
    """
    verifier = _build_jwt_verifier(settings)
    from fastmcp.server.auth import RemoteAuthProvider

    issuer = settings.GF_JWT_ISSUER
    # base_url = the resource's public URL; the audience IS the canonical resource URI
    # and a safe fallback when GF_PUBLIC_BASE_URL is unset (both required non-None here).
    base = settings.GF_PUBLIC_BASE_URL or settings.GF_JWT_AUDIENCE
    assert issuer and base  # guaranteed by _build_jwt_verifier's validation above
    log.info("auth_mode", mode="jwt", issuer=issuer)
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(issuer)],
        base_url=base,
        resource_base_url=settings.GF_PUBLIC_BASE_URL,
    )




def _install_resource_tolerance() -> None:
    """Tolerate a duplicated trailing path segment in the RFC 8707 ``resource``.

    Some MCP clients (e.g. ChatGPT) send ``resource=https://host/mcp/mcp``. The
    strict OAuthProxy check would raise ``AuthorizeError(error="invalid_target")``,
    which the MCP SDK's error enum rejects -> pydantic ValidationError -> the
    authorize handler crashes into a 500. Collapse ``/mcp/mcp`` -> ``/mcp`` in the
    normalizer so client and server resource URLs compare equal. Idempotent.
    """
    import re

    from fastmcp.server.auth.oauth_proxy import proxy as _p

    if getattr(_p, "_gf_resource_tolerant", False):
        return
    _orig = _p._normalize_resource_url

    def _norm(url: str) -> str:
        return re.sub(r"(/[^/]+)\1(?=$|/)", r"\1", _orig(url))

    _p._normalize_resource_url = _norm
    _p._gf_resource_tolerant = True

def _build_oauth(settings: RouterSettings) -> Any:
    # R1.5: OAuthProxy.token_verifier is REQUIRED — so the JWT verifier inputs are
    # mandatory in oauth mode too (no None verifier). base_url MUST be the public URL.
    _install_resource_tolerance()
    required = {
        "GF_OAUTH_CLIENT_ID": settings.GF_OAUTH_CLIENT_ID,
        "GF_OAUTH_CLIENT_SECRET": settings.GF_OAUTH_CLIENT_SECRET,
        "GF_OAUTH_AUTHORIZE_URL": settings.GF_OAUTH_AUTHORIZE_URL,
        "GF_OAUTH_TOKEN_URL": settings.GF_OAUTH_TOKEN_URL,
        "GF_PUBLIC_BASE_URL": settings.GF_PUBLIC_BASE_URL,
        "GF_JWT_ISSUER": settings.GF_JWT_ISSUER,
        "GF_JWT_JWKS_URL": settings.GF_JWT_JWKS_URL,
        "GF_JWT_AUDIENCE": settings.GF_JWT_AUDIENCE,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ConfigurationError(f"oauth mode requires: {', '.join(missing)}")
    from fastmcp.server.auth import MultiAuth, OAuthProxy

    verifier = _build_jwt_verifier(settings)  # raw TokenVerifier for OAuthProxy
    # All four are guaranteed truthy by the missing-check above; assert narrows the
    # str|None settings to str for the type-checker (OAuthProxy requires non-None).
    authorize_url = settings.GF_OAUTH_AUTHORIZE_URL
    token_url = settings.GF_OAUTH_TOKEN_URL
    client_id = settings.GF_OAUTH_CLIENT_ID
    public_base = settings.GF_PUBLIC_BASE_URL
    assert authorize_url and token_url and client_id and public_base
    require_consent = _CONSENT_ARG[settings.GF_OAUTH_REQUIRE_CONSENT]
    oauth = OAuthProxy(
        upstream_authorization_endpoint=authorize_url,
        upstream_token_endpoint=token_url,
        upstream_client_id=client_id,
        upstream_client_secret=settings.GF_OAUTH_CLIENT_SECRET,
        token_verifier=verifier,  # REQUIRED — never None
        base_url=public_base,  # ROOT origin — OAuth endpoints (/authorize, /token) live here
        # resource_base_url = the protected-resource URI (the MCP endpoint), which is
        # ALSO the RFC 8707 `resource` clients send + the minted-token audience. It must
        # equal GF_JWT_AUDIENCE (…/mcp), NOT base_url (root): OAuthProxy validates the
        # client's `resource` param against this, and the MCP path is not threaded into
        # it here, so deriving it from base_url would reject every client with
        # invalid_target. See GF_PUBLIC_BASE_URL vs GF_JWT_AUDIENCE in .env.docker.example.
        resource_base_url=settings.GF_JWT_AUDIENCE,
        # Fixed signing key → the OAuthProxy-minted tokens AND the encrypted on-disk client
        # store (whose dir + Fernet key derive from this) stay valid across restarts and
        # Keycloak client-secret rotation. None falls back to fastmcp's deterministic
        # derive-from-client-secret. Pairs with the persistent FASTMCP_HOME volume (prod
        # compose): without durable storage a stable key alone still loses DCR clients.
        jwt_signing_key=settings.GF_OAUTH_JWT_SIGNING_KEY,
        # Skip fastmcp's own "Allow Access" consent page — Keycloak is the auth + login
        # gate; the proxy's redundant, unstyled interstitial breaks the branded flow.
        require_authorization_consent=require_consent,
    )
    log.info("auth_mode", mode="oauth", provider=settings.GF_OAUTH_PROVIDER)
    # MultiAuth lets M2M JWT + interactive OAuth coexist (spec §9).
    return MultiAuth(server=oauth, verifiers=[verifier])
