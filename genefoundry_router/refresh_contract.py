"""Version gate for router instrumentation of FastMCP refresh internals."""

from __future__ import annotations

import inspect

from fastmcp.server.auth import OAuthProxy

from genefoundry_router.exceptions import ConfigurationError


def validate_fastmcp_refresh_contract() -> None:
    """Fail startup if the pinned FastMCP refresh seams no longer match ours."""
    try:
        load_source = inspect.getsource(OAuthProxy.load_refresh_token)
        exchange_source = inspect.getsource(OAuthProxy.exchange_refresh_token)
    except (OSError, TypeError) as exc:
        raise ConfigurationError("installed FastMCP refresh contract cannot be inspected") from exc
    required_load = ("token=refresh_token",)
    required_exchange = (
        '"Invalid refresh token"',
        '"Refresh token mapping not found"',
        '"Upstream token not found"',
        '"Refresh not supported for this token"',
        'f"Upstream refresh failed: {e}"',
    )
    if any(fragment not in load_source for fragment in required_load) or any(
        fragment not in exchange_source for fragment in required_exchange
    ):
        raise ConfigurationError("installed FastMCP refresh contract is unsupported")
