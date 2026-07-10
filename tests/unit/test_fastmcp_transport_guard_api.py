import inspect

import fastmcp
from fastmcp import FastMCP


def test_fastmcp_344_host_origin_guard_api_is_available() -> None:
    assert tuple(map(int, fastmcp.__version__.split(".")[:3])) >= (3, 4, 4)
    parameters = inspect.signature(FastMCP.http_app).parameters
    assert "host_origin_protection" in parameters
    assert "allowed_hosts" in parameters
    assert "allowed_origins" in parameters
