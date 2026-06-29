from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.registry import BackendDef


async def test_non_default_cache_ttl_still_namespaces(gnomad_fake):
    gateway = FastMCP("genefoundry")
    backend = BackendDef(name="gnomad", url_env="X", namespace="gnomad", cache_ttl=600)
    register_backend(gateway, backend, proxy_target=gnomad_fake)
    async with Client(gateway) as client:
        names = {t.name for t in await client.list_tools()}
    assert "gnomad_get_variant_details" in names
    assert "gnomad_search_genes" in names


def test_register_uses_proxy_provider_for_non_default_ttl(monkeypatch, gnomad_fake):
    from genefoundry_router import composition

    captured = {}
    orig = composition._register_via_provider

    def spy(server, backend, target, timeout=None):
        captured["ttl"] = backend.cache_ttl
        return orig(server, backend, target, timeout)

    monkeypatch.setattr(composition, "_register_via_provider", spy)
    gateway = FastMCP("genefoundry")
    register_backend(
        gateway,
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", cache_ttl=600),
        proxy_target=gnomad_fake,
    )
    assert captured["ttl"] == 600
