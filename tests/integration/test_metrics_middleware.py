from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.observability import TOOL_CALLS, MetricsMiddleware
from genefoundry_router.registry import BackendDef


async def test_tool_call_increments_counter(gnomad_fake):
    gateway = FastMCP("genefoundry")
    gateway.add_middleware(MetricsMiddleware())
    register_backend(
        gateway,
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        proxy_target=gnomad_fake,
    )
    before = TOOL_CALLS.labels(namespace="gnomad")._value.get()
    async with Client(gateway) as client:
        await client.call_tool("gnomad_get_variant_details", {"value": "x"})
    after = TOOL_CALLS.labels(namespace="gnomad")._value.get()
    assert after == before + 1
