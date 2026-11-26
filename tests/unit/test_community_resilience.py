"""Per-backend bulkhead, timeouts and circuit breaker for community backends (Federation v1 §6, #413)."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpcore2
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from prometheus_client import generate_latest

from genefoundry_router import community_breaker, community_guard
from genefoundry_router.composition import register_backend
from genefoundry_router.egress import community_httpx_client_factory, community_transport
from genefoundry_router.observability import METRICS_REGISTRY, register_health
from genefoundry_router.registry import BackendDef
from tests.unit.test_community_guard import URL, Edge, _backend_app, _error, _reply, _Stream


def _backend(name: str, trust: str) -> BackendDef:
    backend = BackendDef(name=name, namespace=name, url_env="X", url=URL)
    object.__setattr__(backend, "trust", trust)
    return backend


@asynccontextmanager
async def _router(**edges: tuple[Edge, str]) -> AsyncIterator[Client[Any]]:
    server = FastMCP("router")
    for name, (edge, trust) in edges.items():

        def factory(edge: Edge = edge, **kwargs: Any) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=edge, **kwargs)

        register_backend(
            server, _backend(name, trust), proxy_target=URL, timeout=5, httpx_client_factory=factory
        )
    async with (
        edges["acme"][0].app.router.lifespan_context(edges["acme"][0].app),
        Client(server) as client,
    ):
        yield client


def _hang(release: asyncio.Event) -> Any:
    async def body() -> AsyncIterator[bytes]:
        await release.wait()
        yield b"{}"

    return lambda body_: httpx2.Response(
        200, headers={"content-type": "application/json"}, stream=_Stream(body())
    )


def _calls(edge: Edge) -> int:
    return edge.calls.count("tools/call")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(community_breaker, "_now", c)
    return c


async def test_fifth_concurrent_call_fails_fast_and_core_latency_is_unchanged() -> None:
    acme, core = Edge(_backend_app()), Edge(_backend_app())
    release = asyncio.Event()
    async with (
        _router(acme=(acme, "community"), core=(core, "core")) as client,
        core.app.router.lifespan_context(core.app),
    ):

        async def core_p95() -> float:
            times = []
            for _ in range(20):
                start = time.perf_counter()
                assert not (await client.call_tool("core_lookup", {})).is_error
                times.append(time.perf_counter() - start)
            return statistics.quantiles(times, n=20)[-1]

        baseline = await core_p95()
        acme.attack["tools/call"] = _hang(release)
        hung = [
            asyncio.create_task(client.call_tool("acme_lookup", {}, raise_on_error=False))
            for _ in range(4)
        ]
        with anyio.fail_after(5):
            while _calls(acme) < 4:
                await asyncio.sleep(0.01)
        start = time.perf_counter()
        fifth = await client.call_tool("acme_lookup", {}, raise_on_error=False)
        assert time.perf_counter() - start < 0.5
        assert _error(fifth)["error_code"] == "unavailable"
        assert _calls(acme) == 4  # no upstream request for the rejected call
        # p95 within 10 %, plus 50 ms of scheduler slack for a loaded CI runner.
        assert await core_p95() <= baseline * 1.1 + 0.05
        release.set()
        await asyncio.gather(*hung, return_exceptions=True)


async def test_byte_trickle_hits_the_total_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    assert community_breaker.TOTAL_TIMEOUT == 12.0
    monkeypatch.setattr(community_breaker, "TOTAL_TIMEOUT", 0.5)
    acme = Edge(_backend_app())

    async def trickle() -> AsyncIterator[bytes]:
        while True:  # a byte every 50 ms never trips a per-read timeout
            yield b" "
            await asyncio.sleep(0.05)

    acme.attack["tools/call"] = lambda body: httpx2.Response(
        200, headers={"content-type": "application/json"}, stream=_Stream(trickle())
    )
    async with _router(acme=(acme, "community")) as client:
        start = time.perf_counter()
        result = await client.call_tool("acme_lookup", {}, raise_on_error=False)
    assert time.perf_counter() - start < 2
    assert _error(result)["error_code"] == "upstream_timeout"


async def test_entry_timeout_is_bounded_and_used() -> None:
    with pytest.raises(ValueError):
        BackendDef(name="a", namespace="a", timeout=30)
    backend = BackendDef(name="a", namespace="a", url_env="X", url=URL, timeout=10)
    object.__setattr__(backend, "trust", "community")
    register_backend(FastMCP("r"), backend, proxy_target=URL, httpx_client_factory=lambda **k: 0)
    assert community_breaker.get("a").timeout == 10


class _BlackHole(httpcore2.AsyncNetworkBackend):
    """An unroutable address: the SYN is never answered."""

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None, **kw: Any
    ) -> Any:
        self.timeouts.append(timeout)
        try:
            with anyio.fail_after(timeout):
                await anyio.sleep_forever()
        except TimeoutError as exc:
            raise httpcore2.ConnectTimeout("connect timed out") from exc

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)


async def test_unroutable_host_fails_at_three_seconds() -> None:
    hole = _BlackHole()
    transport = community_transport(
        resolve=lambda host, port: ["198.51.100.7"], allow=lambda addr: True, inner=hole
    )
    # fastmcp passes its own (30 s connect) timeout; the community factory must override it.
    client = community_httpx_client_factory(transport)(timeout=httpx2.Timeout(30.0, read=300.0))
    start = time.perf_counter()
    with pytest.raises(httpx2.ConnectTimeout):
        await client.get(URL)
    assert 2.8 <= time.perf_counter() - start < 4
    assert hole.timeouts == [3.0]


async def test_breaker_opens_probes_once_and_closes_or_reopens(clock: _Clock) -> None:
    acme = Edge(_backend_app())
    acme.attack["tools/call"] = _reply(500, b"boom")
    async with _router(acme=(acme, "community")) as client:

        async def call() -> Any:
            return await client.call_tool("acme_lookup", {}, raise_on_error=False)

        for _ in range(5):
            assert _error(await call())["error_code"] != "unavailable"
        assert community_breaker.get("acme").state() == "open"
        before = len(acme.calls)
        assert _error(await call())["error_code"] == "unavailable"
        assert len(acme.calls) == before  # no upstream request at all while open

        clock.now += 61  # half-open: one probe, which fails and reopens
        assert community_breaker.get("acme").state() == "half_open"
        assert _error(await call())["error_code"] == "upstream_error"
        assert _calls(acme) == 6
        assert community_breaker.get("acme").state() == "open"
        assert _error(await call())["error_code"] == "unavailable"
        assert _calls(acme) == 6

        clock.now += 61  # the probe succeeds and closes the breaker
        del acme.attack["tools/call"]
        assert not (await call()).is_error
        assert community_breaker.get("acme").state() == "closed"
        assert not (await call()).is_error
        assert _calls(acme) == 8


async def test_only_one_half_open_probe_at_a_time(clock: _Clock) -> None:
    breaker = community_breaker.get("solo")
    for _ in range(5):
        breaker.release(False, breaker.admit())
    clock.now += 61
    probe = breaker.admit()
    assert probe is True
    assert breaker.admit() is None  # a second caller during the probe fails fast
    breaker.release(None, probe)  # a cancelled probe neither closes nor counts
    assert breaker.admit() is True


async def test_healthy_tools_list_does_not_hold_the_breaker_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(community_breaker, "TOTAL_TIMEOUT", 0.2)
    acme = Edge(_backend_app())
    acme.attack["tools/call"] = _hang(asyncio.Event())
    async with _router(acme=(acme, "community")) as client:
        for _ in range(5):
            result = await client.call_tool("acme_lookup", {}, raise_on_error=False)
            assert _error(result)["error_code"] == "upstream_timeout"
            assert await client.list_tools()  # tools/list keeps answering
        assert community_breaker.get("acme").state() == "open"


def test_breaker_state_in_health_and_metrics(clock: _Clock) -> None:
    breaker = community_breaker.get("acme")
    for _ in range(5):
        breaker.release(False, breaker.admit())
    app = FastAPI()
    register_health(app, [_backend("acme", "community"), _backend("core", "core")])
    body = TestClient(app).get("/health").json()
    assert body["backends"]["circuit"] == {"acme": "open"}
    metrics = generate_latest(METRICS_REGISTRY).decode()
    assert 'genefoundry_community_breaker_state{namespace="acme"} 1.0' in metrics
    clock.now += 61
    assert (
        'genefoundry_community_breaker_state{namespace="acme"} 2.0'
        in generate_latest(METRICS_REGISTRY).decode()
    )


def test_core_only_health_has_no_circuit_key() -> None:
    app = FastAPI()
    register_health(app, [_backend("core", "core")])
    assert "circuit" not in TestClient(app).get("/health").json()["backends"]


# --- the byte cap is per JSON-RPC message, not per long-lived SSE stream (#412 follow-up) ---


def _sse(events: list[dict[str, Any]], sep: bytes = b"\r\n") -> bytes:
    return b"".join(
        b"event: message" + sep + b"data: " + json.dumps(e).encode() + sep + sep for e in events
    )


def _sse_reply(padding: int, count: int) -> Any:
    def make(body: dict[str, Any]) -> httpx2.Response:
        notes = [
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "info", "data": "x" * padding},
            }
        ] * count
        reply = {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"result": "ok"},
                "isError": False,
            },
        }
        raw = _sse([*notes, reply])
        chunks = [raw[i : i + 4096] for i in range(0, len(raw), 4096)]  # boundaries split

        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Stream(stream())
        )

    return make


async def test_sse_stream_larger_than_cap_passes_when_every_message_is_small(
    quarantined: list[Any],
) -> None:
    acme = Edge(_backend_app())
    acme.attack["tools/call"] = _sse_reply(padding=10_000, count=40)  # ~400 KB in total
    async with _router(acme=(acme, "community")) as client:
        result = await client.call_tool("acme_lookup", {}, raise_on_error=False)
    assert not result.is_error, result.content
    assert quarantined == []


async def test_one_sse_message_over_the_cap_quarantines(quarantined: list[Any]) -> None:
    acme = Edge(_backend_app())
    acme.attack["tools/call"] = _sse_reply(padding=community_guard.MAX_RESPONSE_BYTES, count=1)
    async with _router(acme=(acme, "community")) as client:
        result = await client.call_tool("acme_lookup", {}, raise_on_error=False)
    # The SDK drops the cause of an SSE read failure, so the fixed code is upstream_error.
    assert _error(result)["error_code"] == "upstream_error"
    assert quarantined == [("acme", "response_cap")]


@pytest.fixture
def quarantined(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(community_guard, "quarantine", lambda ns, reason: seen.append((ns, reason)))
    return seen
