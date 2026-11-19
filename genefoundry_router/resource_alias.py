"""Legacy ChatGPT resource alias for OAuth ``/authorize`` (RFC 8707).

Up to v0.10.3 the router's PRM advertised ``…/mcp/mcp``; ChatGPT workspace apps created
then still send it, and FastMCP rejects it with ``invalid_target``. Exactly that doubled
form (canonical + ``/`` + the canonical's last path segment) is rewritten to the canonical
resource; anything else is left for FastMCP's strict check.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import structlog
from fastmcp.server.auth.identity_assertion import normalize_resource_url
from mcp.server.auth.provider import AuthorizationParams

from genefoundry_router.observability import OAUTH_RESOURCE_ALIAS

log = structlog.get_logger(__name__)


def resolve_resource_alias(
    params: AuthorizationParams, canonical: object, client_class: str
) -> AuthorizationParams:
    """Return ``params`` with the legacy doubled resource replaced by ``canonical``."""
    if not params.resource or canonical is None:
        return params
    canonical_url = str(canonical)
    segment = urlsplit(canonical_url).path.rstrip("/").rsplit("/", 1)[-1]
    doubled = f"{canonical_url.rstrip('/')}/{segment}"
    if not segment or normalize_resource_url(params.resource) != normalize_resource_url(doubled):
        return params
    log.info("oauth_resource_alias", client_class=client_class)
    OAUTH_RESOURCE_ALIAS.labels(client_class=client_class).inc()
    return params.model_copy(update={"resource": canonical_url})
