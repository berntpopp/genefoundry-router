"""Offline document expansion for the BM25 tool index.

BM25 only matches words a tool's own text already contains, so a lay request ("is this
change seen in healthy people?"), a paraphrase, or a non-English query misses the right
tool even when its description is accurate. Document expansion closes that gap offline: an
LLM writes realistic user requests and search keywords per tool ONCE
(``scripts/gen_tool_expansions.py``), and the router folds that text into each tool's index
document. Nothing runs at query time, so the 1-CPU container keeps its sub-millisecond
search. On the held-out discoverability set it lifts hit@5 from 0.68 to 0.94.

The expansion text is index-only: it is never serialized into a ``search_tools`` hit, so a
generated phrase can never reach a model as tool documentation.

File format (``genefoundry_router/data/tool-expansions.json``)::

    {"version": 1,
     "generator": {...provenance...},
     "tools": {"<namespace>_<tool>": {"source_sha256": "<catalog_entry_sha256>",
                                     "queries": [...], "keywords": [...]}}}

``source_sha256`` fingerprints the catalog entry the expansion was generated from, so a
guard test can flag entries that went stale after a backend rewrote its tool.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

BUILTIN = "builtin"
BUILTIN_RESOURCE = "tool-expansions.json"
FORMAT_VERSION = 1
# Bounds keep one tool's expansion from swamping BM25 length normalisation (and bound the
# index size if a regenerated file is ever malformed or adversarial).
MAX_ITEMS_PER_FIELD = 40
MAX_ITEM_CHARS = 200
_FIELDS = ("queries", "keywords")
_SPACE_RE = re.compile(r"\s+")


def catalog_entry_sha256(name: str, description: str, params: Mapping[str, str]) -> str:
    """Stable fingerprint of the catalog fields an expansion was generated from."""
    canon = json.dumps(
        {"name": name, "description": description, "params": dict(sorted(params.items()))},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _clean(item: str) -> str:
    """Replace control/format code points (NUL, bidi overrides, …) with spaces."""
    chars = (" " if unicodedata.category(ch)[0] == "C" else ch for ch in item)
    return _SPACE_RE.sub(" ", "".join(chars)).strip()[:MAX_ITEM_CHARS]


def _validate(raw: Any, origin: str) -> dict[str, dict[str, list[str]]]:
    if not isinstance(raw, dict) or raw.get("version") != FORMAT_VERSION:
        raise ValueError(f"{origin}: expected an object with version {FORMAT_VERSION}")
    tools = raw.get("tools")
    if not isinstance(tools, dict):
        raise ValueError(f"{origin}: 'tools' must be an object keyed by tool name")
    out: dict[str, dict[str, list[str]]] = {}
    for name, entry in tools.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError(f"{origin}: entry {name!r} must be an object")
        fields: dict[str, list[str]] = {}
        for field in _FIELDS:
            items = entry.get(field, [])
            if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                raise ValueError(f"{origin}: {name}.{field} must be a list of strings")
            fields[field] = items
        out[name] = fields
    return out


def load_expansion_file(path: Path) -> dict[str, dict[str, list[str]]]:
    """Parse and validate an expansion file; raise ``ValueError`` when it is unusable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"expansion file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"expansion file is not valid JSON: {path}") from exc
    return _validate(raw, str(path))


def _load_builtin() -> dict[str, dict[str, list[str]]]:
    resource = files("genefoundry_router.data").joinpath(BUILTIN_RESOURCE)
    return _validate(json.loads(resource.read_text(encoding="utf-8")), BUILTIN_RESOURCE)


def expansion_texts(source: str) -> dict[str, str]:
    """Tool name -> flattened expansion text for ``source``.

    ``source`` is ``"builtin"`` (the packaged file), ``"off"``/blank (no expansion), or a
    path to a file in the same format (e.g. one regenerated for a private fleet).
    """
    choice = source.strip()
    if not choice or choice.lower() == "off":
        return {}
    entries = _load_builtin() if choice.lower() == BUILTIN else load_expansion_file(Path(choice))
    texts: dict[str, str] = {}
    for name, fields in entries.items():
        parts = [
            cleaned
            for field in _FIELDS
            for item in fields[field][:MAX_ITEMS_PER_FIELD]
            if (cleaned := _clean(item))
        ]
        if parts:
            texts[name] = " ".join(parts)
    return texts
