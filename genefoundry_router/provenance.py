"""Router endpoint for canonical fleet and tool provenance."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import Response


@lru_cache(maxsize=1)
def _load_cached_provenance_payload() -> tuple[bytes, str, str, str]:
    """Load, validate, and cache the raw immutable provenance bytes and headers."""
    resource = files("genefoundry_router.data").joinpath("fleet-provenance.json")
    raw_bytes = resource.read_bytes()
    parsed = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected dict in {resource}, got {type(parsed)}")
    total_backends = str(parsed.get("fleet", {}).get("total_backends", 0))
    total_tools = str(parsed.get("fleet", {}).get("total_tools", 0))
    digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
    etag = f'"{digest}"'
    return raw_bytes, etag, total_backends, total_tools


def load_fleet_provenance() -> dict[str, Any]:
    """Load the canonical fleet provenance payload as a fresh dictionary."""
    raw_bytes, _, _, _ = _load_cached_provenance_payload()
    payload: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    return payload


def register_provenance(app: FastAPI) -> None:
    """Attach GET /provenance and GET /api/fleet/provenance to the FastAPI application."""

    @app.api_route("/provenance", methods=["GET", "HEAD"], tags=["provenance"])
    @app.api_route("/api/fleet/provenance", methods=["GET", "HEAD"], tags=["provenance"])
    async def get_provenance(request: Request) -> Response:
        """Return the canonical provenance artifact for the GeneFoundry fleet."""
        raw_bytes, etag, backends, tools = _load_cached_provenance_payload()

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=300",
            "ETag": etag,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Expose-Headers": (
                "ETag, X-GeneFoundry-Fleet-Backends, X-GeneFoundry-Fleet-Tools"
            ),
            "X-GeneFoundry-Fleet-Backends": backends,
            "X-GeneFoundry-Fleet-Tools": tools,
        }

        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)

        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)

        return Response(
            content=raw_bytes,
            status_code=200,
            headers=headers,
            media_type="application/json",
        )
