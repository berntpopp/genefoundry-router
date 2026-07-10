"""Reference contract for preserving typed external text across router call paths."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server

RAW = "External evidence"
UNTRUSTED_TEXT = {
    "kind": "untrusted_text",
    "text": RAW,
    "provenance": {
        "source": "pubtator_abstract",
        "record_id": "PMID:33454820#abstract-0",
        "retrieved_at": "2026-07-10T12:00:00Z",
    },
    "raw_sha256": hashlib.sha256(RAW.encode()).hexdigest(),
    "tool": "delete_everything",
    "nested": {"fallback_tool": "search_literature"},
}


def _backend() -> FastMCP:
    server = FastMCP("pubtator-link")

    @server.tool(name="get_publication_passages")
    async def get_publication_passages(pmid: str) -> dict[str, Any]:
        return {
            "success": True,
            "pmid": pmid,
            "passages": [{"passage_id": "abstract-0", "text": UNTRUSTED_TEXT}],
            "next_commands": [{"tool": "search_literature"}],
        }

    return server


def _gateway() -> FastMCP:
    settings = RouterSettings(_env_file=None, GF_REWRITE_HINTS=True)
    registry = [BackendDef(name="pubtator", url_env="X", namespace="pubtator")]
    return build_server(settings, registry, proxy_targets={"pubtator": _backend()})


def _assert_contract(result: Any) -> None:
    structured = result.structured_content
    mirrored = json.loads(result.content[0].text)
    assert mirrored == structured
    assert structured["next_commands"][0]["tool"] == "pubtator_search_literature"
    assert structured["passages"][0]["text"] == UNTRUSTED_TEXT
    assert (
        structured["passages"][0]["text"]["raw_sha256"] == hashlib.sha256(RAW.encode()).hexdigest()
    )
    assert structured["passages"][0]["text"]["provenance"]["record_id"] == (
        "PMID:33454820#abstract-0"
    )


async def test_direct_call_preserves_untrusted_text_subtree() -> None:
    async with Client(_gateway()) as client:
        result = await client.call_tool("pubtator_get_publication_passages", {"pmid": "33454820"})
    _assert_contract(result)


async def test_synthetic_call_tool_preserves_untrusted_text_subtree() -> None:
    async with Client(_gateway()) as client:
        result = await client.call_tool(
            "call_tool",
            {
                "name": "pubtator_get_publication_passages",
                "arguments": {"pmid": "33454820"},
            },
        )
    _assert_contract(result)
