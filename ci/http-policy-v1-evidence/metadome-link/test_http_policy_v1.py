"""Canonical vendored conformance suite for GeneFoundry HTTP Policy Standard v1.

Copy this file byte-for-byte to ``tests/conformance/test_http_policy_v1.py`` in an adopting
backend.  That repository supplies the ``http_policy_adapter`` fixture, which must bind the
backend's real URL guard/client to the small adapter protocol below.  The fixture file's SHA-256
is recorded by the router adoption ledger; do not edit this suite locally.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

import pytest


class HttpPolicyAdapter(Protocol):
    """Test seam that must execute the adopting backend's production policy."""

    def allow(self, url: str) -> object: ...

    def request(self, url: str, redirects: list[str], max_redirects: int) -> None: ...

    def read_decoded(self, chunks: Iterable[bytes], cap: int) -> None: ...

    def is_non_retryable(self, error: Exception) -> bool: ...

    def public_message(self, error: Exception) -> str: ...


def _rejection(operation: Callable[[], object]) -> Exception:
    with pytest.raises(Exception) as captured:
        operation()
    return captured.value


def _assert_safe_non_retryable(adapter: HttpPolicyAdapter, error: Exception) -> None:
    message = adapter.public_message(error)
    assert adapter.is_non_retryable(error)
    assert message
    for forbidden in ("http://", "https://", "allowed.example", "evil.example", ":@"):
        assert forbidden not in message


def test_https_only(http_policy_adapter: HttpPolicyAdapter) -> None:
    error = _rejection(lambda: http_policy_adapter.allow("http://allowed.example/resource"))
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_reject_syntactic_userinfo(http_policy_adapter: HttpPolicyAdapter) -> None:
    error = _rejection(lambda: http_policy_adapter.allow("https://:@allowed.example/resource"))
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_normalized_exact_origin(http_policy_adapter: HttpPolicyAdapter) -> None:
    assert http_policy_adapter.allow(
        "https://allowed.example/resource"
    ) == http_policy_adapter.allow("https://ALLOWED.EXAMPLE:443/another-resource")
    error = _rejection(lambda: http_policy_adapter.allow("https://allowed.example:444/resource"))
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_request_hook_checks_each_redirect_hop(http_policy_adapter: HttpPolicyAdapter) -> None:
    error = _rejection(
        lambda: http_policy_adapter.request(
            "https://allowed.example/start",
            ["https://allowed.example/continue", "https://evil.example/redirected"],
            max_redirects=5,
        )
    )
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_redirect_limit_at_most_five(http_policy_adapter: HttpPolicyAdapter) -> None:
    redirects = [f"https://allowed.example/hop-{index}" for index in range(6)]
    error = _rejection(
        lambda: http_policy_adapter.request(
            "https://allowed.example/start", redirects, max_redirects=5
        )
    )
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_decoded_streaming_byte_cap(http_policy_adapter: HttpPolicyAdapter) -> None:
    error = _rejection(lambda: http_policy_adapter.read_decoded([b"abc", b"def"], cap=5))
    _assert_safe_non_retryable(http_policy_adapter, error)


def test_fixed_host_free_non_retryable_error(http_policy_adapter: HttpPolicyAdapter) -> None:
    error = _rejection(
        lambda: http_policy_adapter.allow("https://evil.example/private?token=secret")
    )
    _assert_safe_non_retryable(http_policy_adapter, error)
