"""Secure-by-default guard: refuse auth=none on a non-loopback bind (R-sec.1)."""

import pytest

from genefoundry_router.cli import is_insecure_public_bind


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_is_secure_without_auth(host: str) -> None:
    # auth=none is fine on loopback — only this host can reach it.
    assert is_insecure_public_bind("none", host, allow_insecure=False) is False


def test_public_bind_without_auth_is_insecure() -> None:
    assert is_insecure_public_bind("none", "0.0.0.0", allow_insecure=False) is True  # noqa: S104


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_public_bind_with_auth_is_secure(mode: str) -> None:
    assert is_insecure_public_bind(mode, "0.0.0.0", allow_insecure=False) is False  # noqa: S104


def test_public_bind_without_auth_allowed_when_overridden() -> None:
    # GF_ALLOW_INSECURE=true is the explicit, logged escape hatch (PoC only).
    assert is_insecure_public_bind("none", "0.0.0.0", allow_insecure=True) is False  # noqa: S104
