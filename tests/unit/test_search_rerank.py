"""System One (Jev / OneJev) reranker: request contract, ranking, abstention, fail-open."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog
from fastmcp.tools.base import Tool

from genefoundry_router.search_rerank import MAX_QUERY_CHARS, RerankConfig, SystemOneReranker

URL = "https://rerank.example/api/v1/systemone"
QUERY = "is this DNA change seen in healthy people from BRCA1 patient 4711"


def _tools(*names: str) -> list[Tool]:
    return [
        Tool(
            name=n,
            description=f"{n} does things.\n  With   whitespace. " + "x" * 600,
            parameters={"type": "object", "properties": {}},
        )
        for n in names
    ]


def _noul_handler(scores: dict[str, float], seen: list[dict[str, Any]]) -> Callable:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"body": body, "headers": dict(request.headers)})
        answers = {
            qid: {"type": "noul", "noul": scores[q["instructions"]["tool"]["name"]]}
            for qid, q in body["questions"].items()
        }
        return httpx.Response(200, json={"model": "m", "answers": answers, "usage": {}})

    return handler


def _reranker(handler: Callable, **overrides: Any) -> SystemOneReranker:
    cfg = RerankConfig(url=URL, model="typesafe/jev-1.13", api_key="sk-test", **overrides)
    return SystemOneReranker(cfg, transport=httpx.MockTransport(handler))


async def test_noul_request_contract_and_ranking() -> None:
    seen: list[dict[str, Any]] = []
    rr = _reranker(_noul_handler({"a_x": 0.2, "b_y": 0.9, "c_z": 0.5}, seen))
    ranked = await rr.rerank(QUERY, _tools("a_x", "b_y", "c_z"))
    assert ranked is not None
    assert [(t.name, s) for t, s in ranked] == [("b_y", 0.9), ("c_z", 0.5), ("a_x", 0.2)]
    body = seen[0]["body"]
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"query": QUERY}
    assert seen[0]["headers"]["authorization"] == "Bearer sk-test"
    q0 = body["questions"]["t0"]
    assert q0["type"] == "noul" and set(q0["criteria"]) == {"true", "false"}
    desc = q0["instructions"]["tool"]["description"]
    assert len(desc) <= 300 and "  " not in desc and "\n" not in desc


async def test_no_authorization_header_without_key() -> None:
    seen: list[dict[str, Any]] = []
    cfg = RerankConfig(url=URL, model="jev-latest", api_key=None)
    handler = _noul_handler({"a_x": 0.5}, seen)
    rr = SystemOneReranker(cfg, transport=httpx.MockTransport(handler))
    assert await rr.rerank(QUERY, _tools("a_x")) is not None
    assert "authorization" not in seen[0]["headers"]


async def test_ties_keep_first_stage_order() -> None:
    rr = _reranker(_noul_handler({"a_x": 0.7, "b_y": 0.7, "c_z": 0.9, "d_w": 0.7}, []))
    ranked = await rr.rerank(QUERY, _tools("a_x", "b_y", "c_z", "d_w"))
    assert ranked is not None
    assert [t.name for t, _ in ranked] == ["c_z", "a_x", "b_y", "d_w"]


async def test_min_score_filters_and_can_abstain() -> None:
    rr = _reranker(_noul_handler({"a_x": 0.1, "b_y": 0.45, "c_z": 0.2}, []), min_score=0.3)
    ranked = await rr.rerank(QUERY, _tools("a_x", "b_y", "c_z"))
    assert ranked is not None and [t.name for t, _ in ranked] == ["b_y"]
    abstain = _reranker(_noul_handler({"a_x": 0.1, "b_y": 0.2}, []), min_score=0.3)
    assert await abstain.rerank(QUERY, _tools("a_x", "b_y")) == []


async def test_choice_request_contract() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        q = body["questions"]["tool"]
        assert q["type"] == "choice" and list(q["criteria"]) == ["a_x", "b_y", "c_z"]
        probs = {"a_x": 0.1, "b_y": 0.0, "c_z": 0.9}
        answers = {"tool": {"type": "choice", "choice": "c_z", "probabilities": probs}}
        return httpx.Response(200, json={"answers": answers})

    rr = _reranker(handler, question="choice")
    ranked = await rr.rerank(QUERY, _tools("a_x", "b_y", "c_z"))
    assert ranked is not None
    assert [t.name for t, _ in ranked] == ["c_z", "a_x", "b_y"]
    assert len(seen) == 1


def test_choice_rejects_min_score() -> None:
    with pytest.raises(ValueError, match="noul"):
        RerankConfig(url=URL, model="m", question="choice", min_score=0.3)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(401, json={"error": "bad key"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"error": {"message": "HTTP 400: upstream"}}),
        httpx.Response(200, json={"answers": {"t0": {"noul": 0.4}}}),  # t1 missing
        httpx.Response(200, json={"answers": {"t0": {"noul": "high"}, "t1": {"noul": 0.1}}}),
        httpx.Response(200, json={"answers": {"t0": {"noul": 1.7}, "t1": {"noul": 0.1}}}),
        httpx.Response(200, content=b'{"answers": {"t0": {"noul": NaN}, "t1": {"noul": 0}}}'),
        httpx.Response(200, json={"answers": {"t0": {"noul": True}, "t1": {"noul": 0}}}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_bad_responses_fail_open(response: httpx.Response) -> None:
    rr = _reranker(lambda _r: response)
    assert await rr.rerank(QUERY, _tools("a_x", "b_y")) is None


async def test_redirects_are_not_followed() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://evil.example/steal"})

    rr = _reranker(handler)
    assert await rr.rerank(QUERY, _tools("a_x")) is None
    assert hits == [URL]  # the key was sent only to the configured endpoint


async def test_transport_errors_fail_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    rr = _reranker(handler)
    assert await rr.rerank(QUERY, _tools("a_x")) is None


async def test_failure_opens_circuit_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    healthy = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if not healthy["on"]:
            return httpx.Response(503)
        return _noul_handler({"a_x": 0.8}, [])(request)

    clock = {"t": 1000.0}
    monkeypatch.setattr("genefoundry_router.search_rerank.time.monotonic", lambda: clock["t"])
    rr = _reranker(handler, cooldown=30.0)
    assert await rr.rerank(QUERY, _tools("a_x")) is None
    assert calls["n"] == 1
    healthy["on"] = True
    assert await rr.rerank(QUERY, _tools("a_x")) is None  # still cooling down: no call
    assert calls["n"] == 1
    clock["t"] += 31.0
    assert await rr.rerank(QUERY, _tools("a_x")) is not None
    assert calls["n"] == 2


async def test_empty_candidates_and_blank_query_skip_the_call() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the reranker")

    rr = _reranker(handler)
    assert await rr.rerank(QUERY, []) == []
    assert await rr.rerank("   ", _tools("a_x")) is None


async def test_failure_logs_never_contain_query_or_key(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"echo {QUERY} sk-test")

    caplog.set_level(logging.DEBUG)
    rr = _reranker(handler)
    with structlog.testing.capture_logs() as logs:
        assert await rr.rerank(QUERY, _tools("a_x")) is None
    rendered = repr(logs) + caplog.text
    assert any(e["event"] == "search_rerank_failed" for e in logs)
    assert "4711" not in rendered and "BRCA1" not in rendered
    assert "sk-test" not in rendered


def test_config_repr_hides_api_key() -> None:
    cfg = RerankConfig(url=URL, model="m", api_key="sk-very-secret")
    assert "sk-very-secret" not in repr(cfg)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": "ftp://x/systemone"},
        {"url": "not a url"},
        {"timeout": 0},
        {"min_score": 1.5},
        {"min_score": -0.1},
        {"question": "score"},
    ],
)
def test_config_validation(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {"url": URL, "model": "m"}
    with pytest.raises(ValueError):
        RerankConfig(**{**base, **kwargs})


async def test_overall_deadline_bounds_a_slow_trickle() -> None:
    import asyncio

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.5)  # each phase could be fast; the total is what's bounded
            return httpx.Response(200, json={"answers": {"t0": {"noul": 0.9}}})

    cfg = RerankConfig(url=URL, model="m", timeout=0.05)
    rr = SystemOneReranker(cfg, transport=Slow())
    start = __import__("time").perf_counter()
    assert await rr.rerank(QUERY, _tools("a_x")) is None
    assert __import__("time").perf_counter() - start < 0.4


@pytest.mark.parametrize(
    ("response", "opens"),
    [
        (httpx.Response(503), True),
        (httpx.Response(429), True),
        (httpx.Response(401), True),
        (httpx.Response(400, json={"error": "max_tokens_exceeded"}), False),
        (httpx.Response(422), False),
        (httpx.Response(200, json={"answers": {}}), False),
    ],
)
async def test_only_endpoint_faults_open_the_circuit(response: httpx.Response, opens: bool) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return response

    rr = _reranker(handler)
    assert await rr.rerank(QUERY, _tools("a_x")) is None
    assert await rr.rerank(QUERY, _tools("a_x")) is None
    # a per-request fault (one caller's input) must not switch reranking off for everyone
    assert calls["n"] == (1 if opens else 2)


async def test_query_sent_upstream_is_capped() -> None:
    seen: list[dict[str, Any]] = []
    rr = _reranker(_noul_handler({"a_x": 0.5}, seen))
    assert await rr.rerank("variant " * 2000, _tools("a_x")) is not None
    assert len(seen[0]["body"]["state"]["query"]) <= MAX_QUERY_CHARS


async def test_choice_answer_missing_candidates_falls_back() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        probs = {"A": 0.7, "B": 0.3}  # keyed by letters, not the tool names we sent
        return httpx.Response(200, json={"answers": {"tool": {"probabilities": probs}}})

    rr = _reranker(handler, question="choice")
    assert await rr.rerank(QUERY, _tools("a_x", "b_y")) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://user:sk-secret@jev.example/v1/systemone",
        "https://jev.example/v1/systemone?key=sk-secret",
    ],
)
def test_urls_carrying_credentials_are_rejected(url: str) -> None:
    with pytest.raises(ValueError) as exc:
        RerankConfig(url=url, model="m")
    assert "sk-secret" not in str(exc.value)


async def test_aclose_releases_the_client() -> None:
    rr = _reranker(_noul_handler({"a_x": 0.5}, []))
    await rr.aclose()
    assert rr._client.is_closed
