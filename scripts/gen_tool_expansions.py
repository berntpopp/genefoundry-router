#!/usr/bin/env python3
"""Generate the offline BM25 document expansions (``genefoundry_router/data/tool-expansions.json``).

For every tool in the catalog snapshot, one or more LLMs (via OpenRouter) write realistic
search requests + keywords for which THAT tool is the right answer. The generator sees only
the tool's own catalog entry and its sibling tools' first description lines — never an
evaluation task — so the discoverability benchmarks stay held-out. Two model families are
merged by default so the index is not tuned to one model's phrasing.

Incremental by default: entries whose ``source_sha256`` still matches the catalog entry are
kept, removed tools are dropped, and only new or changed tools are sent to the LLMs.
Responses are cached on disk (keyed by model + prompt), so re-runs are free.

    OPENROUTER_API_KEY=... uv run python scripts/gen_tool_expansions.py            # update stale
    uv run python scripts/gen_tool_expansions.py --check                           # CI-style: exit 1 if stale
    uv run python scripts/gen_tool_expansions.py --all                             # regenerate everything

Run it after ``make snapshot-catalog`` (``make expansions``). The API key is read from the
environment and never printed or written.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from genefoundry_router.search_expansion import (  # noqa: E402
    FORMAT_VERSION,
    catalog_entry_sha256,
)

DEFAULT_CATALOG = ROOT / "tests" / "discoverability" / "catalog.json"
DEFAULT_OUT = ROOT / "genefoundry_router" / "data" / "tool-expansions.json"
DEFAULT_CACHE = ROOT / ".cache" / "tool-expansions"
DEFAULT_MODELS = ("qwen/qwen3.7-plus", "google/gemini-3.8-flash")
# Some reasoning models refuse to run with reasoning disabled.
REASONING = {"google/gemini-3.8-flash": {"effort": "low"}}
PROMPT_VERSION = "v1"


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n")[0][:160]


def build_prompt(tool: dict[str, Any], siblings: list[dict[str, Any]]) -> str:
    """The generation prompt. Changing it invalidates the response cache (by design)."""
    sib = "\n".join(
        f"- {s['name']}: {_first_line(s['description'])}" for s in siblings if s is not tool
    )
    params = "\n".join(f"- {k}: {v}" for k, v in tool["params"].items())
    return f"""You help build a search index for a biomedical/genomics API tool catalog (~285 tools behind one gateway: gnomAD, ClinVar, VEP, SpliceAI, HPO, Mondo, Orphanet, GTEx, UniProt, STRING, PubTator, GeneReviews, ClinGen, PanelApp, etc.).

For the TARGET tool below, write what real users would type into a tool-search box when THIS tool is the right one to call.

TARGET TOOL
name: {tool["name"]}
tags: {", ".join(tool.get("tags") or [])}
description:
{(tool.get("description") or "").strip()}
parameters:
{params}

Other tools from the same backend (write queries that fit the TARGET better than these siblings):
{sib or "(none)"}

Return ONLY JSON: {{"queries": [...10 strings...], "keywords": [...10-15 strings...]}}
queries: 10 diverse, realistic requests: 2-3 from a clinician/patient in lay wording, 3 from a bioinformatician using jargon and abbreviations, 3 concrete requests naming example entities (real gene symbols, variants like 17-43045712-G-A or NM_000059.4:c.68_69del, rsIDs, HPO/MONDO IDs, diseases, tissues), 1 in German or French. Vary length and phrasing; do not copy the tool name.
keywords: synonyms, abbreviations, alternative database/field names and related terms a user might search with that are NOT already obvious from the name (e.g. "AF", "MAF", "allele freq", "popmax")."""


def _generate(
    client: httpx.Client, base: str, model: str, prompt: str, cache: Path, name: str
) -> dict[str, list[str]]:
    digest = hashlib.sha256((model + prompt).encode()).hexdigest()[:16]
    cached = cache / f"{name}-{digest}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["parsed"]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
        "reasoning": REASONING.get(model, {"enabled": False}),
    }
    last = "unknown error"
    for attempt in range(4):
        try:
            resp = client.post(f"{base}/chat/completions", json=body)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{.*\}", text, re.S)
            parsed = json.loads(match.group(0) if match else text)
            if isinstance(parsed.get("queries"), list) and isinstance(parsed.get("keywords"), list):
                parsed = {
                    k: [x.strip() for x in parsed[k] if isinstance(x, str) and x.strip()]
                    for k in ("queries", "keywords")
                }
                cached.write_text(json.dumps({"model": model, "parsed": parsed}), encoding="utf-8")
                return parsed
            last = "response missing queries/keywords"
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            last = type(exc).__name__  # never echo bodies: they could carry request headers
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{model} failed for {name}: {last}")


def _merge(parts: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {"queries": [], "keywords": []}
    for field in merged:
        seen: set[str] = set()
        for part in parts:
            for item in part.get(field, []):
                key = " ".join(item.lower().split())
                if key and key not in seen:
                    seen.add(key)
                    merged[field].append(item.strip())
    return merged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--all", action="store_true", help="regenerate every entry")
    ap.add_argument("--check", action="store_true", help="report staleness; exit 1 if stale")
    args = ap.parse_args(argv)

    catalog: list[dict[str, Any]] = json.loads(args.catalog.read_text(encoding="utf-8"))
    by_ns: dict[str, list[dict[str, Any]]] = {}
    for tool in catalog:
        by_ns.setdefault(tool["name"].split("_", 1)[0], []).append(tool)
    shas = {
        t["name"]: catalog_entry_sha256(t["name"], t["description"] or "", t["params"])
        for t in catalog
    }
    existing: dict[str, Any] = {}
    if args.out.exists():
        existing = json.loads(args.out.read_text(encoding="utf-8")).get("tools", {})
    todo = [
        t
        for t in catalog
        if args.all or existing.get(t["name"], {}).get("source_sha256") != shas[t["name"]]
    ]
    removed = sorted(set(existing) - set(shas))
    print(
        f"catalog={len(catalog)} up-to-date={len(catalog) - len(todo)} "
        f"to-generate={len(todo)} removed={len(removed)}"
    )
    if args.check:
        return 1 if todo or removed else 0

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tools = {k: v for k, v in existing.items() if k in shas}
    if todo:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("OPENROUTER_API_KEY is not set", file=sys.stderr)
            return 2
        base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=180.0, headers=headers) as client:

            def work(tool: dict[str, Any]) -> tuple[str, dict[str, list[str]]]:
                prompt = build_prompt(tool, by_ns[tool["name"].split("_", 1)[0]])
                parts = [
                    _generate(client, base, m, prompt, args.cache_dir, tool["name"]) for m in models
                ]
                return tool["name"], _merge(parts)

            with cf.ThreadPoolExecutor(8) as pool:
                for name, merged in pool.map(work, todo):
                    tools[name] = {"source_sha256": shas[name], **merged}

    previous: dict[str, Any] = {}
    if args.out.exists():
        previous = json.loads(args.out.read_text(encoding="utf-8")).get("generator", {})
    if not todo and not removed and previous:
        print("nothing to regenerate; expansion file unchanged")
        return 0
    # Provenance records every model that produced entries still in the file.
    models = list(dict.fromkeys([*previous.get("models", []), *models])) if not args.all else models
    doc = {
        "version": FORMAT_VERSION,
        "generator": {
            "script": "scripts/gen_tool_expansions.py",
            "prompt_version": PROMPT_VERSION,
            "models": models,
            "catalog": str(args.catalog.relative_to(ROOT))
            if args.catalog.is_relative_to(ROOT)
            else args.catalog.name,
            "updated": datetime.now(UTC).date().isoformat(),
        },
        "tools": dict(sorted(tools.items())),
    }
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: "
        f"{len(tools)} tools"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
