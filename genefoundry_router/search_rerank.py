"""Optional second-stage reranker over the BM25 shortlist via the System One API.

TypeSafe's Jev (hosted: TypeSafe directly or OpenRouter's passthrough) and the open OneJev
models (self-hosted, e.g. on a GPU VPS) all speak the same ``POST …/systemone`` contract:
a ``state`` plus a map of typed questions, answered with calibrated probabilities in one
forward pass. The router asks one yes/no ("noul") question per BM25 candidate — "can this
tool directly fulfil the request?" — and reorders by P(yes). On the held-out
discoverability set this lifted hit@1 from 0.72 to 0.89 over expanded BM25, and a P(yes)
threshold rejected every out-of-scope request while keeping the in-scope hits.

Design constraints:

* **Off by default** and never required: the router stays a thin CPU service.
* **Fail open**: any error, timeout, or malformed answer returns ``None`` and the caller
  serves the BM25 order; a short cooldown stops a dead endpoint from adding its timeout to
  every search.
* **Privacy**: the query goes to the configured endpoint (a third party when hosted), so
  enabling this is an explicit deployment decision. Nothing here logs the query, the
  response body, or the key — failures log only the error class and HTTP status.
* **No token passthrough**: the endpoint key is the router's own setting; the caller's
  ``Authorization`` header is never forwarded.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
import structlog
from fastmcp.tools.base import Tool
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

if TYPE_CHECKING:
    from genefoundry_router.config import RouterSettings

NOUL_INSTRUCTION = "Can `tool` directly fulfil the user's request in `query`?"
NOUL_CRITERIA = {
    "true": "Calling this tool would directly return what the request asks for.",
    "false": "The tool is unrelated, only tangential, or an administrative/utility tool.",
}
CHOICE_INSTRUCTION = (
    "Which of these tools should an agent call to fulfil the user's request in `query`?"
)
MAX_CHOICE_OPTIONS = 255  # System One hard limit per Choice question
# Longer queries are cut before leaving the router: bounds upstream cost per search and stops
# one caller's oversized input from turning into an upstream 4xx.
MAX_QUERY_CHARS = 2000
# Failures that mean "the endpoint is unhealthy" (open the cooldown for everyone). Any other
# 4xx or a malformed answer is a per-request fault: fall back for that search only, so one
# caller cannot switch reranking off for all users.
_CIRCUIT_STATUSES = frozenset({401, 403, 408, 429})


class Reranker(Protocol):
    """Second-stage ranker: ``None`` = keep BM25 order, ``[]`` = nothing fits."""

    async def rerank(
        self, query: str, tools: Sequence[Tool]
    ) -> list[tuple[Tool, float]] | None: ...


class RerankConfig(BaseModel):
    """Validated reranker settings (built from ``GF_SEARCH_RERANK_*``)."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    url: str  # full endpoint, e.g. https://openrouter.ai/api/v1/systemone
    model: str = "jev-latest"
    api_key: SecretStr | None = None
    question: Literal["noul", "choice"] = "noul"
    timeout: float = Field(default=3.0, gt=0)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cooldown: float = Field(default=30.0, ge=0.0)
    max_description_chars: int = Field(default=300, ge=40)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("rerank url must be an absolute http(s) URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            # credentials belong in the api_key setting, never in a URL that gets logged
            raise ValueError("rerank url must not carry credentials, a query, or a fragment")
        return value.strip()

    @model_validator(mode="after")
    def _threshold_needs_noul(self) -> RerankConfig:
        # Choice probabilities are relative (they sum to 1 across the shortlist), so an
        # absolute "nothing fits" threshold is only meaningful for independent nouls.
        if self.question == "choice" and self.min_score > 0:
            raise ValueError("min_score requires question='noul'")
        return self


def _describe(tool: Tool, limit: int) -> str:
    return " ".join((tool.description or "").split())[:limit]


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("score is not a number")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("score outside [0, 1]")
    return score


class SystemOneReranker:
    """Rerank BM25 candidates with a System One model; ``None`` means "use BM25 order"."""

    def __init__(self, config: RerankConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        headers = {"Content-Type": "application/json"}
        if config.api_key is not None:
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout),
            headers=headers,
            transport=transport,
            follow_redirects=False,  # never replay the key to a redirect target
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=30),
        )
        self._open_until = 0.0

    def _request(self, query: str, tools: Sequence[Tool]) -> dict[str, Any]:
        limit = self.config.max_description_chars
        if self.config.question == "choice":
            options = {t.name: _describe(t, limit) for t in tools}
            questions: dict[str, Any] = {
                "tool": {"type": "choice", "instructions": CHOICE_INSTRUCTION, "criteria": options}
            }
        else:
            questions = {
                f"t{i}": {
                    "type": "noul",
                    "instructions": {
                        "tool": {"name": t.name, "description": _describe(t, limit)},
                        "question": NOUL_INSTRUCTION,
                    },
                    "criteria": NOUL_CRITERIA,
                }
                for i, t in enumerate(tools)
            }
        state = {"query": query[:MAX_QUERY_CHARS]}
        return {"model": self.config.model, "state": state, "questions": questions}

    def _scores(self, payload: Any, tools: Sequence[Tool]) -> list[float]:
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            raise ValueError("response has no answers")
        if self.config.question == "choice":
            probs = (answers.get("tool") or {}).get("probabilities")
            if not isinstance(probs, dict) or any(t.name not in probs for t in tools):
                raise ValueError("choice answer does not score every candidate")
            return [_probability(probs[t.name]) for t in tools]
        out = []
        for i in range(len(tools)):
            answer = answers.get(f"t{i}")
            if not isinstance(answer, dict):
                raise ValueError("noul answer missing")
            out.append(_probability(answer.get("noul")))
        return out

    async def _call(self, query: str, tools: Sequence[Tool]) -> list[float]:
        response = await self._client.post(self.config.url, json=self._request(query, tools))
        response.raise_for_status()
        return self._scores(response.json(), tools)

    async def aclose(self) -> None:
        """Release the pooled connections (called from the router's lifespan teardown)."""
        await self._client.aclose()

    async def rerank(self, query: str, tools: Sequence[Tool]) -> list[tuple[Tool, float]] | None:
        """Candidates sorted by model score (ties keep BM25 order), filtered by
        ``min_score``. ``[]`` means "nothing fits"; ``None`` means the stage was skipped or
        failed and the caller should fall back to the BM25 ranking."""
        if not tools:
            return []
        if not query.strip() or len(tools) > MAX_CHOICE_OPTIONS:
            return None
        if time.monotonic() < self._open_until:
            return None
        try:
            # httpx timeouts are per phase; this bounds the whole round-trip.
            scores = await asyncio.wait_for(self._call(query, tools), self.config.timeout)
        except Exception as exc:  # fail open on anything: search must keep working
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            endpoint_fault = (
                isinstance(exc, httpx.TransportError | TimeoutError)
                or status in _CIRCUIT_STATUSES
                or (status is not None and status >= 500)
            )
            if endpoint_fault:
                self._open_until = time.monotonic() + self.config.cooldown
            structlog.get_logger(__name__).warning(
                "search_rerank_failed",
                error=type(exc).__name__,
                status=status,
                candidates=len(tools),
                cooldown_s=self.config.cooldown if endpoint_fault else 0,
            )
            return None
        order = sorted(range(len(tools)), key=lambda i: -scores[i])  # stable: BM25 tie-break
        return [(tools[i], scores[i]) for i in order if scores[i] >= self.config.min_score]


def rerank_config(settings: RouterSettings) -> RerankConfig:
    """Build the validated reranker config from ``GF_SEARCH_RERANK_*`` settings."""
    return RerankConfig(
        url=settings.GF_SEARCH_RERANK_URL or "",
        model=settings.GF_SEARCH_RERANK_MODEL,
        api_key=settings.GF_SEARCH_RERANK_API_KEY,
        question=settings.GF_SEARCH_RERANK_QUESTION,
        timeout=settings.GF_SEARCH_RERANK_TIMEOUT,
        min_score=settings.GF_SEARCH_RERANK_MIN_SCORE,
    )


def build_reranker(settings: RouterSettings) -> SystemOneReranker | None:
    """The configured reranker, or ``None`` when ``GF_SEARCH_RERANK=off``."""
    if settings.GF_SEARCH_RERANK == "off":
        return None
    return SystemOneReranker(rerank_config(settings))
