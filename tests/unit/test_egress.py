"""SSRF-safe community egress (Federation Standard v1 §6, issue #409). No network needed."""

from __future__ import annotations

import asyncio
import datetime
import ssl
from typing import Any
from unittest.mock import MagicMock

import httpcore2
import httpx2
import pytest
from fastmcp import FastMCP

from genefoundry_router import composition
from genefoundry_router.composition import default_httpx_client_factory, register_backend
from genefoundry_router.egress import (
    InvalidResponseError,
    check_community_url,
    community_httpx_client_factory,
    community_transport,
)
from genefoundry_router.registry import BackendDef

URL = "https://community.example/mcp"
PUBLIC = "93.184.215.14"


class SpyBackend(httpcore2.AsyncNetworkBackend):
    """Inner network backend that records every connect attempt and never opens a socket."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def connect_tcp(self, host: str, port: int, **_: Any) -> httpcore2.AsyncNetworkStream:
        self.calls.append((host, port))
        raise httpcore2.ConnectError("spy: no socket")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _resolver(*answers: list[str]) -> Any:
    """Fake resolver returning successive answers (the last one repeats)."""
    seq = list(answers)

    def resolve(host: str, port: int) -> list[str]:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return resolve


async def _get(transport: httpx2.AsyncBaseTransport, url: str = URL) -> httpx2.Response:
    async with community_httpx_client_factory(transport)() as client:
        return await client.post(url, json={})


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "169.254.169.254",
        "fd00::1",
        "fe80::1",
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind
        "224.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "2002:a00:1::1",  # 6to4 wrapping 10.0.0.1
        "239.1.2.3",
    ],
)
async def test_refused_address_opens_no_socket(addr: str) -> None:
    spy = SpyBackend()
    with pytest.raises(httpx2.ConnectError, match="refused"):
        await _get(community_transport(resolve=_resolver([addr]), inner=spy))
    assert spy.calls == []


async def test_public_address_is_dialled_as_ip_literal() -> None:
    spy = SpyBackend()
    with pytest.raises(httpx2.ConnectError, match="spy"):
        await _get(community_transport(resolve=_resolver([PUBLIC]), inner=spy))
    assert spy.calls == [(PUBLIC, 443)]


async def test_mixed_dns_answer_is_refused() -> None:
    spy = SpyBackend()
    with pytest.raises(httpx2.ConnectError, match="refused"):
        await _get(community_transport(resolve=_resolver([PUBLIC, "10.0.0.1"]), inner=spy))
    assert spy.calls == []


async def test_dns_rebinding_is_refused_at_call_time() -> None:
    resolve = _resolver([PUBLIC], ["127.0.0.1"])
    check_community_url(URL, resolve=resolve)  # public at load
    spy = SpyBackend()
    with pytest.raises(httpx2.ConnectError, match="refused"):
        await _get(community_transport(resolve=resolve, inner=spy))
    assert spy.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://community.example/mcp",
        "https://community.example:8443/mcp",
        "https://community.example/",
        "https://community.example/api",
        "https://127.0.0.1/mcp",
    ],
)
def test_bad_url_rejected_at_load(url: str) -> None:
    with pytest.raises(ValueError):
        check_community_url(url, resolve=_resolver([PUBLIC]))


def test_private_dns_rejected_at_load() -> None:
    with pytest.raises(ValueError):
        check_community_url(URL, resolve=_resolver(["10.0.0.1"]))


async def test_non_443_port_refused_at_connect() -> None:
    spy = SpyBackend()
    with pytest.raises(httpx2.ConnectError, match="refused"):
        await _get(
            community_transport(resolve=_resolver([PUBLIC]), inner=spy),
            URL.replace("example/", "example:8443/"),
        )
    assert spy.calls == []


async def test_redirect_is_invalid_response_without_second_request() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(307, headers={"location": "https://169.254.169.254/latest"})

    with pytest.raises(InvalidResponseError) as info:
        await _get(httpx2.MockTransport(handler))
    assert info.value.error_code == "invalid_response"
    assert seen == [URL]


def test_follow_redirects_locked_off_for_core_and_community() -> None:
    for factory in (default_httpx_client_factory(), community_httpx_client_factory()):
        client = factory(headers={}, follow_redirects=True)
        assert client.follow_redirects is False
        assert client.trust_env is False


def _backend(**extra: Any) -> Any:
    backend = BackendDef(name="x", namespace="x", url_env="X_URL", url=URL)
    for key, value in extra.items():
        object.__setattr__(backend, key, value)
    return backend


@pytest.mark.parametrize(
    ("trust", "community"), [(None, False), ("core", False), ("community", True)]
)
def test_composition_selects_community_client_only_for_community(
    monkeypatch: pytest.MonkeyPatch, trust: str | None, community: bool
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        composition,
        "make_proxy_client",
        lambda *a, httpx_client_factory=None, **k: captured.append(httpx_client_factory),
    )
    backend = _backend() if trust is None else _backend(trust=trust)
    providers: list[Any] = []
    server = MagicMock(spec=FastMCP)
    server.add_provider.side_effect = lambda provider, namespace: providers.append(provider)
    register_backend(server, backend)
    providers[0].client_factory()
    factory = captured[0]
    if community:
        assert getattr(factory, "community", False) is True
    else:
        assert factory is None  # core keeps the shared-pool default


# --- TLS: real handshake against a local self-signed server ------------------------------


def _self_signed(hostname: str) -> tuple[bytes, bytes]:
    crypto = pytest.importorskip("cryptography")
    del crypto
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


class _PortShift(httpcore2.AsyncNetworkBackend):
    """Dial the local test server's port instead of 443; everything else is real."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.inner = httpcore2.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, **kw: Any) -> httpcore2.AsyncNetworkStream:
        assert host == "127.0.0.1" and port == 443
        return await self.inner.connect_tcp(host, self.port, **kw)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def test_tls_sni_and_host_preserved_and_bad_cert_fails(tmp_path: Any) -> None:
    host = "community.test"
    cert_pem, key_pem = _self_signed(host)
    (tmp_path / "c.pem").write_bytes(cert_pem)
    (tmp_path / "k.pem").write_bytes(key_pem)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    sni: list[str | None] = []
    server_ctx.sni_callback = lambda sock, name, ctx: sni.append(name)  # type: ignore[assignment]
    hosts: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            hosts.extend(
                line.split(":", 1)[1].strip()
                for line in head.split("\r\n")
                if line.lower().startswith("host:")
            )
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")
            await writer.drain()
        except ssl.SSLError, asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
    port = srv.sockets[0].getsockname()[1]
    resolve = _resolver(["127.0.0.1"])
    allow_local = lambda ip: True  # noqa: E731 - test-only IP check override
    trusted = ssl.create_default_context(cafile=str(tmp_path / "c.pem"))
    url = f"https://{host}/mcp"
    async with srv:
        ok = await _get(
            community_transport(
                resolve=resolve, inner=_PortShift(port), allow=allow_local, verify=trusted
            ),
            url,
        )
        assert ok.status_code == 200
        assert sni == [host]
        assert hosts == [host]
        with pytest.raises(httpx2.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
            await _get(
                community_transport(resolve=resolve, inner=_PortShift(port), allow=allow_local),
                url,
            )
