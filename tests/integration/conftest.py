"""In-process FastMCP fake backends for integration tests (no network)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from genefoundry_router.devtools.fakes import make_fake_backend

__all__ = ["make_fake_backend"]


@pytest.fixture
def gnomad_fake() -> FastMCP:
    return make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])


@pytest.fixture
def gtex_fake() -> FastMCP:
    return make_fake_backend("gtex-link", ["get_gene_information", "search_genes"])


@pytest.fixture
def pubtator_fake() -> FastMCP:
    return make_fake_backend(
        "pubtator-link", ["pubtator_search_literature", "pubtator_get_passages"]
    )


@pytest.fixture
def pubtator_clean_fake() -> FastMCP:
    # post Tool-Naming Standard v1 (pubtator-link#57): clean, unprefixed leaf names
    return make_fake_backend("pubtator-link", ["search_literature", "get_passages"])
