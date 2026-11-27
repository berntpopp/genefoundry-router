"""Search labelling and ranking guards for community backends (Federation Standard v1 §7).

A community tool is indexed within the core description budget, without its tags or any
expansion, and padded so a cut document gains nothing from BM25 length normalisation; core
wins any score within 10 %; at most half of the served hits are community; and every
community hit says it leaves GeneFoundry. Core-only catalogs never reach this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from fastmcp.server.transforms.search.base import _extract_searchable_text
from fastmcp.server.transforms.search.bm25 import _BM25Index, _tokenize
from fastmcp.tools.base import Tool

from genefoundry_router.registry import BackendDef
from genefoundry_router.schema_slim import (
    PARAM_DESCRIPTION_CHARS,
    TOOL_DESCRIPTION_CHARS,
    _truncate,
)

TIE = 1.1  # a community score is divided by this before ranking
DOC_TOKENS = 300  # padding floor, the skill-document floor of tool_search


class WeightedBM25Index(_BM25Index):
    """FastMCP's BM25 Okapi with a per-document score divisor."""

    def __init__(self, k1: float, b: float, divisors: list[float]) -> None:
        super().__init__(k1, b)
        self.divisors = divisors

    def raw_scores(self, text: str) -> list[float]:
        """Plain BM25 Okapi score of every document."""
        scores = [0.0] * self._n
        for token in _tokenize(text):
            if token not in self._df:
                continue
            idf = math.log((self._n - self._df[token] + 0.5) / (self._df[token] + 0.5) + 1.0)
            for i, tf_doc in enumerate(self._tf):
                tf = tf_doc.get(token, 0)
                if tf:
                    norm = 1 - self.b + self.b * self._doc_lengths[i] / self._avg_dl
                    scores[i] += idf * tf * (self.k1 + 1) / (tf + self.k1 * norm)
        return scores

    def query(self, text: str, top_k: int) -> list[int]:
        raw = self.raw_scores(text)
        scores = [s / d for s, d in zip(raw, self.divisors, strict=True)]
        ranked = sorted(range(self._n), key=lambda i: scores[i], reverse=True)
        return [i for i in ranked[:top_k] if scores[i] > 0]


class CommunitySearch:
    """The community entries of one search transform, keyed by namespace."""

    def __init__(self, backends: Sequence[BackendDef]) -> None:
        self.backends = {b.namespace: b for b in backends}

    def __bool__(self) -> bool:
        return bool(self.backends)

    def backend_of(self, tool: Tool) -> BackendDef | None:
        return self.backends.get(tool.name.partition("_")[0])

    def document(self, tool: Tool, fields: str, stem: Callable[[str], str]) -> str:
        """Index text from the budgeted definition plus the boosted name ``fields`` only."""
        params = tool.parameters or {}
        props = {
            name: {"description": _truncate(str(p.get("description", "")), PARAM_DESCRIPTION_CHARS)}
            for name, p in (params.get("properties") or {}).items()
            if isinstance(p, dict)
        }
        budgeted = tool.model_copy(
            update={
                "description": _truncate(tool.description or "", TOOL_DESCRIPTION_CHARS),
                "parameters": {"type": "object", "properties": props},
            }
        )
        text = stem(_extract_searchable_text(budgeted) + fields)
        return text + " communitypad" * max(0, DOC_TOKENS - len(text.split()))

    def index(self, k1: float, b: float, tools: Sequence[Tool]) -> WeightedBM25Index:
        return WeightedBM25Index(k1, b, [TIE if self.backend_of(t) else 1.0 for t in tools])

    def cap(self, ranked: list[Tool], served: int) -> list[Tool]:
        """Drop community hits past half of ``served``."""
        left, kept = served // 2, []
        for tool in ranked:
            if self.backend_of(tool) is not None:
                if not left:
                    continue
                left -= 1
            kept.append(tool)
        return kept

    def label(self, hits: list[Any], tools: Sequence[Tool]) -> None:
        """Mark community hits ``trust``, ``leaves_genefoundry`` and ``data_egress``."""
        for hit, tool in zip(hits, tools, strict=True):
            backend = self.backend_of(tool)
            if backend is not None and isinstance(hit, dict):
                egress = backend.data_egress
                hit["trust"] = "community"
                hit["leaves_genefoundry"] = True
                hit["data_egress"] = egress.model_dump() if egress else None
