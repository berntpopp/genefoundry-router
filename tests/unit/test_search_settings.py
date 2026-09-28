"""GF_SEARCH_* settings: expansion source and the optional System One reranker."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from genefoundry_router.config import RouterSettings
from genefoundry_router.search_rerank import SystemOneReranker, build_reranker

URL = "https://openrouter.ai/api/v1/systemone"


@pytest.fixture(autouse=True)
def _no_ambient_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's exported GF_SEARCH_* must not change what these tests measure."""
    for key in [k for k in os.environ if k.startswith("GF_SEARCH_")]:
        monkeypatch.delenv(key)


def _settings(**kw: object) -> RouterSettings:
    return RouterSettings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_search_defaults() -> None:
    s = _settings()
    assert s.GF_SEARCH_EXPANSIONS == "builtin"
    assert s.GF_SEARCH_RERANK == "off"
    assert s.GF_SEARCH_RERANK_POOL == 20
    assert s.GF_SEARCH_RERANK_MIN_SCORE == 0.0
    assert s.GF_SEARCH_RERANK_QUESTION == "noul"
    assert build_reranker(s) is None


def test_rerank_requires_url() -> None:
    with pytest.raises(ValidationError, match="GF_SEARCH_RERANK_URL"):
        _settings(GF_SEARCH_RERANK="systemone")


def test_hosted_jev_via_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GF_SEARCH_RERANK", "systemone")
    monkeypatch.setenv("GF_SEARCH_RERANK_URL", URL)
    monkeypatch.setenv("GF_SEARCH_RERANK_MODEL", "typesafe/jev-1.13")
    monkeypatch.setenv("GF_SEARCH_RERANK_API_KEY", "sk-or-secret")
    monkeypatch.setenv("GF_SEARCH_RERANK_MIN_SCORE", "0.3")
    s = _settings()
    assert "sk-or-secret" not in repr(s)
    rr = build_reranker(s)
    assert isinstance(rr, SystemOneReranker)
    assert rr.config.model == "typesafe/jev-1.13"
    assert rr.config.min_score == 0.3
    assert rr.config.api_key is not None
    assert rr.config.api_key.get_secret_value() == "sk-or-secret"


def test_self_hosted_onejev_without_key() -> None:
    s = _settings(
        GF_SEARCH_RERANK="systemone",
        GF_SEARCH_RERANK_URL="http://onejev:8000/v1/systemone",
        GF_SEARCH_RERANK_API_KEY="  ",
        GF_SEARCH_RERANK_QUESTION="choice",
    )
    assert s.GF_SEARCH_RERANK_API_KEY is None
    rr = build_reranker(s)
    assert rr is not None and rr.config.api_key is None and rr.config.question == "choice"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"GF_SEARCH_RERANK_POOL": 3}, "GF_SEARCH_RERANK_POOL"),  # below max results (5)
        ({"GF_SEARCH_RERANK_POOL": 256}, "GF_SEARCH_RERANK_POOL"),
        ({"GF_SEARCH_RERANK_URL": "ftp://x/systemone"}, "http"),
        ({"GF_SEARCH_RERANK_MIN_SCORE": 1.2}, "less than or equal"),
        ({"GF_SEARCH_RERANK_TIMEOUT": 0}, "greater than"),
        ({"GF_SEARCH_RERANK_QUESTION": "choice", "GF_SEARCH_RERANK_MIN_SCORE": 0.3}, "noul"),
    ],
)
def test_invalid_rerank_settings_fail_at_startup(overrides: dict[str, object], match: str) -> None:
    base: dict[str, object] = {"GF_SEARCH_RERANK": "systemone", "GF_SEARCH_RERANK_URL": URL}
    with pytest.raises(ValidationError, match=match):
        _settings(**{**base, **overrides})


def test_rerank_settings_ignored_when_off() -> None:
    # A half-filled profile must not block startup while the stage is disabled.
    s = _settings(GF_SEARCH_RERANK="off", GF_SEARCH_RERANK_URL="ftp://nope")
    assert build_reranker(s) is None


def test_unknown_expansion_source_fails_at_startup(tmp_path: object) -> None:
    with pytest.raises(ValidationError, match="expansion"):
        _settings(GF_SEARCH_EXPANSIONS="/nonexistent/tool-expansions.json")


def test_startup_errors_never_echo_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-or-v1-abcdef1234567890SECRETTAIL"  # noqa: S105 - test fixture data
    monkeypatch.setenv("GF_SEARCH_RERANK", "systemone")
    monkeypatch.setenv("GF_SEARCH_RERANK_API_KEY", secret)  # no URL -> validation error
    with pytest.raises(ValidationError) as exc:
        _settings()
    rendered = str(exc.value) + repr(exc.value)
    assert "SECRETTAIL" not in rendered and "abcdef" not in rendered


def test_nested_rerank_errors_never_echo_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-or-v1-abcdef1234567890SECRETTAIL"  # noqa: S105 - test fixture data
    with pytest.raises(ValidationError) as exc:
        _settings(
            GF_SEARCH_RERANK="systemone",
            GF_SEARCH_RERANK_URL=URL,
            GF_SEARCH_RERANK_API_KEY=secret,
            GF_SEARCH_RERANK_TIMEOUT=0,
        )
    assert "SECRETTAIL" not in str(exc.value)
