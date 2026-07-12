"""Secure-by-default guard: refuse auth=none on a non-loopback bind (R-sec.1)."""

import pytest

from genefoundry_router.cli import (
    is_insecure_public_bind,
    refuses_no_rate_limit,
    refuses_public_metrics_without_token,
    should_warn_no_metrics_token,
    should_warn_no_rate_limit,
)

# A routable (non-loopback) bind and a dummy token, used deliberately by these guard tests.
_PUBLIC = "0.0.0.0"  # noqa: S104 - deliberate: exercises the non-loopback guard path
_TOKEN = "s3cret"  # noqa: S105 - dummy metrics token, not a real secret


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_is_secure_without_auth(host: str) -> None:
    # auth=none is fine on loopback — only this host can reach it.
    assert is_insecure_public_bind("none", host, allow_insecure=False) is False


def test_public_bind_without_auth_is_insecure() -> None:
    assert is_insecure_public_bind("none", _PUBLIC, allow_insecure=False) is True


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_public_bind_with_auth_is_secure(mode: str) -> None:
    assert is_insecure_public_bind(mode, _PUBLIC, allow_insecure=False) is False


def test_public_bind_without_auth_allowed_when_overridden() -> None:
    # GF_ALLOW_INSECURE=true is the explicit, logged escape hatch (PoC only).
    assert is_insecure_public_bind("none", _PUBLIC, allow_insecure=True) is False


# D10 / M7: warn (non-breaking) when an authenticated, publicly-reachable deployment
# runs with no per-client rate limit (GF_RATE_LIMIT_RPM=0) — fleet egress-IP abuse risk.
@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_warn_when_public_auth_deployment_has_no_rate_limit(mode: str) -> None:
    assert should_warn_no_rate_limit(mode, _PUBLIC, rate_limit_rpm=0) is True


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_no_warn_when_public_auth_deployment_rate_limited(mode: str) -> None:
    assert should_warn_no_rate_limit(mode, _PUBLIC, rate_limit_rpm=120) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_warn_on_loopback_even_without_rate_limit(host: str) -> None:
    # Loopback is not publicly reachable, so the missing rate limit is not a fleet risk.
    assert should_warn_no_rate_limit("jwt", host, rate_limit_rpm=0) is False


def test_no_rate_limit_warning_when_auth_none() -> None:
    # auth=none is already handled by the insecure-bind guard/warning; don't double-warn.
    assert should_warn_no_rate_limit("none", _PUBLIC, rate_limit_rpm=0) is False


# F-21: an authenticated, publicly-reachable ("production") bind must FAIL CLOSED (not just warn)
# when it has no positive rate limit or would expose /metrics without a token. GF_ALLOW_INSECURE
# downgrades both to the existing warnings for local/PoC use.
@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_refuse_public_auth_bind_without_rate_limit(mode: str) -> None:
    assert refuses_no_rate_limit(mode, _PUBLIC, rate_limit_rpm=0, allow_insecure=False) is True


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_no_refuse_when_public_auth_bind_is_rate_limited(mode: str) -> None:
    assert refuses_no_rate_limit(mode, _PUBLIC, rate_limit_rpm=120, allow_insecure=False) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_refuse_rate_limit_on_loopback(host: str) -> None:
    assert refuses_no_rate_limit("jwt", host, rate_limit_rpm=0, allow_insecure=False) is False


def test_no_refuse_rate_limit_when_auth_none() -> None:
    # Handled by the insecure-bind guard; don't also refuse here.
    assert refuses_no_rate_limit("none", _PUBLIC, rate_limit_rpm=0, allow_insecure=False) is False


def test_rate_limit_refusal_downgraded_by_allow_insecure() -> None:
    assert refuses_no_rate_limit("jwt", _PUBLIC, rate_limit_rpm=0, allow_insecure=True) is False


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_refuse_public_metrics_without_token(mode: str) -> None:
    assert (
        refuses_public_metrics_without_token(
            mode, _PUBLIC, metrics_token=None, allow_insecure=False
        )
        is True
    )


def test_no_refuse_metrics_with_token() -> None:
    assert (
        refuses_public_metrics_without_token(
            "jwt", _PUBLIC, metrics_token=_TOKEN, allow_insecure=False
        )
        is False
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_refuse_metrics_on_loopback(host: str) -> None:
    assert (
        refuses_public_metrics_without_token("jwt", host, metrics_token=None, allow_insecure=False)
        is False
    )


def test_metrics_refusal_downgraded_by_allow_insecure() -> None:
    assert (
        refuses_public_metrics_without_token(
            "jwt", _PUBLIC, metrics_token=None, allow_insecure=True
        )
        is False
    )


# F-21: once GF_ALLOW_INSECURE downgrades the metrics refusal, a PoC operator must still be warned
# that /metrics is public (symmetry with the rate-limit warning).
@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_warn_when_public_auth_bind_exposes_metrics_without_token(mode: str) -> None:
    assert should_warn_no_metrics_token(mode, _PUBLIC, metrics_token=None) is True


def test_no_metrics_warn_when_token_set() -> None:
    assert should_warn_no_metrics_token("jwt", _PUBLIC, metrics_token=_TOKEN) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_metrics_warn_on_loopback(host: str) -> None:
    assert should_warn_no_metrics_token("jwt", host, metrics_token=None) is False


def test_no_metrics_warn_when_auth_none() -> None:
    assert should_warn_no_metrics_token("none", _PUBLIC, metrics_token=None) is False
