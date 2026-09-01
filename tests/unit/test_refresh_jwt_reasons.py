"""``jwt_invalid`` must stop meaning five different things.

98% of every refresh failure the router has ever recorded (149 of 152, across all three
client classes) landed in the single reason ``jwt_invalid``, produced by a bare
``except Exception`` around ``JWTIssuer.verify_token``. That one bucket provably held at
least two unrelated situations at the same time:

* a ChatGPT client whose freshly-issued refresh token simply aged out ~28 minutes after a
  successful authorization — benign, expected, "re-authorise"; and
* ``codex-mcp-client/0.147.0`` replaying ONE dead token 116 times over 45 hours, never once
  starting an authorization — a stuck client burning requests against a public endpoint.

It also cannot be told apart from an issuer or audience misconfiguration, which is the
failure class that actually broke this service twice (2026-07-15 and 2026-08-07). Any alert
threshold that catches the real fault also fires on routine expiry, so the metric was
unusable.

These tests mint REAL tokens with the INSTALLED ``fastmcp.server.auth.jwt_issuer.JWTIssuer``
and drive the real ``verify_token``. That matters: ``JWTIssuer`` raises a *bare* ``JoseError``
for its own claim checks, so those causes are only distinguishable by the exception's
``description``. Pinning that text against a mocked exception would prove nothing; pinning it
against the installed library means a fastmcp upgrade that rewords a message fails HERE,
loudly, instead of silently collapsing the vocabulary back to one bucket.

OWASP's logging guidance is the frame: log enough to diagnose, never the credential itself.
Every reason below is a fixed, closed-vocabulary string — no claim value, no token, no
fragment of either — so the diagnosis becomes legible without the secret becoming loggable.
"""

from __future__ import annotations

import pytest
from fastmcp.server.auth.jwt_issuer import JWTIssuer
from joserfc.errors import BadSignatureError, DecodeError, JoseError

from genefoundry_router.refresh_jwt_reasons import classify_jwt_failure
from genefoundry_router.refresh_models import FAILURE_REASONS

ISSUER = "https://genefoundry.org"
AUDIENCE = "https://genefoundry.org/mcp"
KEY = b"test-key-with-at-least-32-bytes!!"
OTHER_KEY = b"another-key-with-32-bytes-here!!!"


def _issuer(*, issuer: str = ISSUER, audience: str = AUDIENCE, key: bytes = KEY) -> JWTIssuer:
    return JWTIssuer(issuer=issuer, audience=audience, signing_key=key)


def _verify_failure(token: str, verifier: JWTIssuer | None = None) -> BaseException:
    """Run the REAL verify_token and hand back whatever it raised."""
    with pytest.raises(BaseException) as excinfo:  # the exception IS the subject here
        (verifier or _issuer()).verify_token(token, expected_token_use="refresh")  # noqa: S106
    return excinfo.value


def _refresh(issuer: JWTIssuer, *, expires_in: int = 3600) -> str:
    return issuer.issue_refresh_token(
        client_id="client-1", scopes=["openid"], jti="jti-1", expires_in=expires_in
    )


def test_expired_token_is_not_jwt_invalid() -> None:
    """The benign majority. Expiry is the normal end of a refresh token's life and means
    "re-authorise" — it must not share a bucket with a signing-key or audience fault."""
    exc = _verify_failure(_refresh(_issuer(), expires_in=-10))
    assert isinstance(exc, JoseError)
    assert classify_jwt_failure(exc) == "jwt_expired"


def test_issuer_mismatch_is_distinguishable() -> None:
    """Regression fence for the 2026-08-07 incident class."""
    token = _refresh(_issuer(issuer="https://evil.example"))
    assert classify_jwt_failure(_verify_failure(token)) == "jwt_issuer_mismatch"


def test_audience_mismatch_is_distinguishable() -> None:
    """Regression fence for the 2026-07-15 incident class."""
    token = _refresh(_issuer(audience="https://evil.example/mcp"))
    assert classify_jwt_failure(_verify_failure(token)) == "jwt_audience_mismatch"


def test_token_use_mismatch_is_distinguishable() -> None:
    """An access token presented as a refresh token is a client bug, not a bad token."""
    access = _issuer().issue_access_token(
        client_id="client-1", scopes=["openid"], jti="jti-1", expires_in=3600
    )
    assert classify_jwt_failure(_verify_failure(access)) == "jwt_token_use_mismatch"


def test_bad_signature_is_distinguishable() -> None:
    """A token signed with a different key: key rotation, or a forgery."""
    token = _refresh(_issuer(key=OTHER_KEY))
    exc = _verify_failure(token)
    assert isinstance(exc, BadSignatureError)
    assert classify_jwt_failure(exc) == "jwt_signature_invalid"


@pytest.mark.parametrize("garbage", ["not-a-jwt", "a.b.c", "raw-invalid-refresh-secret", ""])
def test_malformed_token_is_distinguishable(garbage: str) -> None:
    """A value that is not a JWT at all — an opaque token, a truncated one, junk."""
    exc = _verify_failure(garbage)
    assert isinstance(exc, DecodeError)
    assert classify_jwt_failure(exc) == "jwt_malformed"


def test_non_jose_error_is_an_internal_fault_not_a_client_token_problem() -> None:
    """The bare ``except Exception`` also swallowed keying/JWKS faults inside the issuer and
    reported them as a bad client token. They are ours, and must say so."""
    assert classify_jwt_failure(RuntimeError("keystore unavailable")) == "internal_error"
    assert classify_jwt_failure(MemoryError()) == "internal_error"


def test_unrecognised_jose_error_degrades_to_jwt_invalid() -> None:
    """A wording change upstream must degrade to today's behaviour, never crash and never
    mislabel: an unrecognised JoseError keeps the historical ``jwt_invalid``."""
    assert classify_jwt_failure(JoseError("some future claim rule")) == "jwt_invalid"


def test_every_reason_is_in_the_bounded_vocabulary() -> None:
    """The reason becomes a Prometheus label and a ledger column, so it must be closed."""
    samples: list[BaseException] = [
        _verify_failure(_refresh(_issuer(), expires_in=-10)),
        _verify_failure(_refresh(_issuer(issuer="https://evil.example"))),
        _verify_failure(_refresh(_issuer(audience="https://evil.example/mcp"))),
        _verify_failure(_refresh(_issuer(key=OTHER_KEY))),
        _verify_failure("not-a-jwt"),
        JoseError("unknown"),
        RuntimeError("boom"),
    ]
    for exc in samples:
        assert classify_jwt_failure(exc) in FAILURE_REASONS


def test_reason_never_carries_token_or_claim_material() -> None:
    """The whole point of the old collapse was privacy. Widening the vocabulary must not
    widen what is written: a reason is a fixed label, never derived from the token."""
    secret_issuer = _issuer(issuer="https://tenant-9f3c-secret.example")
    exc = _verify_failure(_refresh(secret_issuer))
    reason = classify_jwt_failure(exc)

    assert reason == "jwt_issuer_mismatch"
    assert "tenant-9f3c-secret" not in reason
    assert "https" not in reason


def test_a_valid_token_verifies(caplog: pytest.LogCaptureFixture) -> None:
    """Non-vacuity guard: the fixtures above really do produce VALID tokens except for the
    single defect under test, so each assertion isolates that defect."""
    issuer = _issuer()
    payload = issuer.verify_token(_refresh(issuer), expected_token_use="refresh")  # noqa: S106
    assert payload["client_id"] == "client-1"
    assert payload["iss"] == ISSUER
    assert payload["aud"] == AUDIENCE
