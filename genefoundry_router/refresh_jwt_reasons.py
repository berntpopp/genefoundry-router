"""Bounded reasons for a FastMCP refresh-token verification failure.

``OAuthProxy.load_refresh_token`` used to wrap ``JWTIssuer.verify_token`` in a bare
``except Exception`` and record the single reason ``jwt_invalid``. That collapsed FIVE
distinct causes into one non-actionable bucket, which is where 149 of 152 recorded refresh
failures (98%) ended up:

============================  =====================================================
cause                          what an operator should do
============================  =====================================================
``jwt_expired``                nothing — the token aged out; the client re-authorises
``jwt_issuer_mismatch``        FIX CONFIG — the 2026-08-07 incident class
``jwt_audience_mismatch``      FIX CONFIG — the 2026-07-15 incident class
``jwt_token_use_mismatch``     client bug: an access token sent as a refresh token
``jwt_signature_invalid``      signing-key rotation, or a forged token
``jwt_malformed``              not a JWT at all (opaque/truncated/garbage value)
============================  =====================================================

Expiry is high-volume steady-state background, so any alert threshold that caught the two
config faults also fired on it. Splitting the bucket is what makes the failure alertable.

**How the split is derived.** ``joserfc`` raises typed errors for the cryptographic and
structural failures (``BadSignatureError``, ``DecodeError``), so those are matched by TYPE.
FastMCP's own claim checks, however, raise a *bare* ``JoseError`` whose only distinguishing
feature is its ``description`` string (verified against the installed
``fastmcp.server.auth.jwt_issuer``), so those are matched by description — deliberately, and
with a fence: ``tests/unit/test_refresh_jwt_reasons.py`` mints real tokens with the installed
``JWTIssuer`` and drives the real ``verify_token``, so an upstream rewording fails the suite
loudly instead of silently re-collapsing the vocabulary. An unrecognised ``JoseError`` falls
back to the historical ``jwt_invalid``, so that failure mode degrades to today's behaviour
rather than crashing or mislabelling.

**Privacy.** Every value returned here is a fixed literal from a closed vocabulary. Nothing
is derived from the token, its claims, or the exception's own text — the reason becomes a
Prometheus label and a ledger column, and both must stay bounded and free of caller data.

**Known limit.** The router signs its own tokens with HS256, a symmetric key, so a token
signed by a rotated key and a forged token are the same observation. ``jwt_signature_invalid``
covers both and cannot separate them; what it CAN do is keep either of them out of the
expiry/audience buckets, which is the distinction the incidents needed.

**Ordering note.** FastMCP validates token_use, then exp, then iss, then aud, and stops at
the first failure. A token that is both expired and wrong-audience therefore reports
``jwt_expired``. That is the library's precedence, not a choice made here.
"""

from __future__ import annotations

from joserfc.errors import BadSignatureError, DecodeError, ExpiredTokenError, JoseError

# Exception TYPE -> reason. Preferred over text matching wherever the library is specific.
_REASON_BY_TYPE: tuple[tuple[type[BaseException], str], ...] = (
    (BadSignatureError, "jwt_signature_invalid"),
    (DecodeError, "jwt_malformed"),
    # joserfc's own claim validator, if a future fastmcp delegates expiry to it.
    (ExpiredTokenError, "jwt_expired"),
)

# ``JoseError.description`` -> reason, for FastMCP's hand-rolled claim checks, which raise a
# bare ``JoseError``. Pinned against fastmcp/server/auth/jwt_issuer.py::verify_token.
_REASON_BY_DESCRIPTION_PREFIX: tuple[tuple[str, str], ...] = (
    ("Token type mismatch", "jwt_token_use_mismatch"),
    ("Token has expired", "jwt_expired"),
    ("Invalid token issuer", "jwt_issuer_mismatch"),
    ("Invalid token audience", "jwt_audience_mismatch"),
)

#: Reasons this module can return. A subset of ``refresh_models.FAILURE_REASONS``; the test
#: suite asserts containment so the two cannot drift apart.
JWT_FAILURE_REASONS: frozenset[str] = frozenset(
    {reason for _, reason in _REASON_BY_TYPE}
    | {reason for _, reason in _REASON_BY_DESCRIPTION_PREFIX}
    | {"jwt_invalid", "internal_error"}
)


def classify_jwt_failure(exc: BaseException) -> str:
    """Map a ``verify_token`` failure to one bounded, caller-data-free reason.

    A non-``JoseError`` is an internal fault of ours (a keying/JWKS error inside the issuer,
    say), not a client token problem, and is reported as ``internal_error`` — the old bare
    ``except Exception`` mislabelled those as ``jwt_invalid`` too.
    """
    if not isinstance(exc, JoseError):
        return "internal_error"
    for error_type, reason in _REASON_BY_TYPE:
        if isinstance(exc, error_type):
            return reason
    description = getattr(exc, "description", "") or ""
    for prefix, reason in _REASON_BY_DESCRIPTION_PREFIX:
        if description.startswith(prefix):
            return reason
    return "jwt_invalid"
