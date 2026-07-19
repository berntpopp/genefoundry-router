from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import docs.conformance.contract_truth as contract_truth
from docs.conformance.contract_truth import (
    Finding,
    active_markdown_files,
    historical_markdown_files,
    lint_repository,
    main,
)

CATALOG = {
    "search_genes": {"inputSchema": {"properties": {"query": {}, "limit": {}}}},
    "get_gene": {"inputSchema": {"properties": {"symbol": {}}}},
}

CANONICAL_DISCLAIMER = (
    "Research use only. Not clinical decision support. Do not use for diagnosis, "
    "treatment, triage, or patient management."
)


def write(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lint_reports_an_unknown_keyword_with_file_line_and_tool(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "README.md",
        '# Calls\n\nUse `search_genes(query="BRCA1", page=1)`.\n',
    )

    findings = lint_repository(tmp_path, CATALOG)

    assert findings == [
        Finding(
            path=Path("README.md"),
            line=3,
            rule="unknown-argument",
            message=("search_genes.page is absent from the live inputSchema.properties"),
        )
    ]


def test_lint_accepts_known_keywords(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        'Use `search_genes(query="BRCA1", limit=5)`.\n',
    )

    assert lint_repository(tmp_path, CATALOG) == []


@pytest.mark.parametrize(
    "call",
    [
        "requests.get(timeout=5)",
        'unknown_tool(query="BRCA1", page=1)',
    ],
)
def test_unknown_and_non_tool_calls_are_ignored(tmp_path: Path, call: str) -> None:
    write(tmp_path, "README.md", f"`{call}`\n")

    assert lint_repository(tmp_path, CATALOG) == []


def test_active_markdown_discovery_is_nested_and_excludes_internal_roots(
    tmp_path: Path,
) -> None:
    expected = [
        write(tmp_path, "README.md", "# Readme\n"),
        write(tmp_path, "CHANGELOG.md", "# Changelog\n"),
        write(tmp_path, "docs/guide.md", "# Guide\n"),
        write(tmp_path, "docs/guides/nested/setup.md", "# Setup\n"),
    ]
    write(tmp_path, "OTHER.md", "# Not active\n")
    write(tmp_path, "docs/specs/undated.md", "# Excluded\n")
    write(tmp_path, "docs/plans/nested/notes.md", "# Excluded\n")
    write(tmp_path, "docs/superpowers/notes.md", "# Excluded\n")
    write(tmp_path, "docs/reviews/review.md", "# Excluded\n")

    assert active_markdown_files(tmp_path) == sorted(expected)


def test_historical_discovery_only_returns_dated_internal_records(
    tmp_path: Path,
) -> None:
    expected = [
        write(tmp_path, "docs/specs/2026-07-18-design.md", "# Design\n"),
        write(tmp_path, "docs/plans/2026-07-18-plan.md", "# Plan\n"),
        write(
            tmp_path,
            "docs/superpowers/specs/2026-07-18-design.md",
            "# Design\n",
        ),
    ]
    write(tmp_path, "docs/specs/design.md", "# Not dated\n")
    write(tmp_path, "docs/guide/2026-07-18-guide.md", "# Active\n")

    assert historical_markdown_files(tmp_path) == sorted(expected)


@pytest.mark.parametrize(
    "preamble",
    [
        "# Design\n\n",
        "---\nowner: fleet\n---\n\n# Design\n\n**Date:** 2026-07-18\n**Status:** Approved\n\n",
    ],
)
@pytest.mark.parametrize(
    "marker",
    [
        "> Historical record\n",
        "> Historical record — historical context\n",
    ],
)
def test_historical_record_accepts_valid_first_prose_marker(
    tmp_path: Path,
    preamble: str,
    marker: str,
) -> None:
    write(
        tmp_path,
        "docs/specs/2026-07-18-design.md",
        f"{preamble}{marker}\nCurrent prose.\n",
    )

    assert lint_repository(tmp_path, CATALOG) == []


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("# Design\n\nCurrent prose.\n", 3),
        ("# Design\n\n> Historical records are useful.\n", 3),
        ("# Design\n\n> Historical record - old context\n", 3),
    ],
)
def test_historical_record_rejects_missing_or_malformed_marker(
    tmp_path: Path,
    body: str,
    line: int,
) -> None:
    write(tmp_path, "docs/plans/2026-07-18-plan.md", body)

    assert lint_repository(tmp_path, CATALOG) == [
        Finding(
            path=Path("docs/plans/2026-07-18-plan.md"),
            line=line,
            rule="historical-record-marker",
            message=(
                "first prose block must begin with '> Historical record' "
                "or '> Historical record — …'"
            ),
        )
    ]


@pytest.mark.parametrize(
    "claim",
    [
        "Every response includes a provenance object.",
        "Every MCP tool response includes provenance.",
        "EVERY MCP ENVELOPE CONTAINS a status field.",
        "All tools return the standard response envelope.",
        ("Every response includes provenance, while all tools except get_gene return envelopes."),
        "Every response includes provenance, except.",
    ],
)
def test_lint_rejects_unqualified_universal_response_claims(
    tmp_path: Path,
    claim: str,
) -> None:
    write(tmp_path, "docs/guide.md", f"# Guide\n\n{claim}\n")

    assert lint_repository(tmp_path, CATALOG) == [
        Finding(
            path=Path("docs/guide.md"),
            line=3,
            rule="universal-response-claim",
            message="unqualified universal MCP response or envelope claim",
        )
    ]


@pytest.mark.parametrize(
    "claim",
    [
        "Not every response includes a provenance object.",
        "All tools except get_gene return the standard response envelope.",
        "All MCP tools except get_gene return the standard response envelope.",
        "Every response except health includes a provenance object.",
        "All tools return the standard envelope except get_gene.",
    ],
)
def test_lint_accepts_negated_or_explicitly_qualified_claims(
    tmp_path: Path,
    claim: str,
) -> None:
    write(tmp_path, "README.md", f"{claim}\n")

    assert lint_repository(tmp_path, CATALOG) == []


@pytest.mark.parametrize(
    "claim",
    [
        ("Every response except health includes provenance, but all tools return envelopes."),
        ("All tools return envelopes, but every response except health includes provenance."),
    ],
)
def test_lint_evaluates_each_universal_claim_in_a_clause(
    tmp_path: Path,
    claim: str,
) -> None:
    write(tmp_path, "README.md", f"{claim}\n")

    assert lint_repository(tmp_path, CATALOG) == [
        Finding(
            path=Path("README.md"),
            line=1,
            rule="universal-response-claim",
            message="unqualified universal MCP response or envelope claim",
        )
    ]


def test_lint_ignores_unrelated_all_tools_claim(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "All tools have documented parameters.\n")

    assert lint_repository(tmp_path, CATALOG) == []


def test_lint_accepts_only_the_exact_canonical_disclaimer(tmp_path: Path) -> None:
    disclaimer_block = (
        "> [!IMPORTANT]\n"
        "> Research use only. Not clinical decision support. Do not use for diagnosis,\n"
        "> treatment, triage, or patient management.\n"
    )
    write(tmp_path, "README.md", disclaimer_block)

    assert lint_repository(tmp_path, CATALOG) == []
    assert contract_truth._is_allowlisted_prose_block(disclaimer_block)
    assert not contract_truth._is_allowlisted_prose_block(
        disclaimer_block.replace("patient management", "clinical care")
    )


def test_disclaimer_does_not_suppress_an_universal_claim(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        f"{CANONICAL_DISCLAIMER} All tools return the same envelope.\n",
    )

    assert lint_repository(tmp_path, CATALOG) == [
        Finding(
            path=Path("README.md"),
            line=1,
            rule="universal-response-claim",
            message="unqualified universal MCP response or envelope claim",
        )
    ]


def test_findings_have_stable_path_line_rule_message_order(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/z.md",
        "All tools return one envelope. search_genes(zzz=1, aaa=2)\n",
    )
    write(tmp_path, "README.md", "search_genes(page=1)\n")

    findings = lint_repository(tmp_path, CATALOG)

    assert findings == sorted(
        findings,
        key=lambda finding: (
            finding.path.as_posix(),
            finding.line,
            finding.rule,
            finding.message,
        ),
    )
    assert [finding.path.as_posix() for finding in findings] == [
        "README.md",
        "docs/z.md",
        "docs/z.md",
        "docs/z.md",
    ]


def test_cli_prints_findings_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "README.md", "search_genes(page=1)\n")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(CATALOG), encoding="utf-8")

    result = main(["--root", str(tmp_path), "--catalog", str(catalog_path)])

    assert result == 1
    assert capsys.readouterr().out == (
        "README.md:1: unknown-argument: search_genes.page is absent from the live "
        "inputSchema.properties\n"
    )


def test_cli_returns_zero_without_findings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(tmp_path, "README.md", 'search_genes(query="BRCA1")\n')
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(CATALOG), encoding="utf-8")

    result = main(["--root", str(tmp_path), "--catalog", str(catalog_path)])

    assert result == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("root_kind", ["missing", "file"])
def test_cli_rejects_a_root_that_is_not_a_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    root_kind: str,
) -> None:
    root = tmp_path / root_kind
    if root_kind == "file":
        root.write_text("not a directory\n", encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(CATALOG), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), "--catalog", str(catalog_path)])

    assert error.value.code == 2
    assert "--root must be an existing directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    "malformed_tool",
    [
        [],
        {},
        {"inputSchema": []},
        {"inputSchema": {"properties": []}},
    ],
)
@pytest.mark.parametrize("documented_call", ["", 'search_genes(query="BRCA1")\n'])
def test_cli_rejects_malformed_catalog_entries_before_linting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    malformed_tool: object,
    documented_call: str,
) -> None:
    write(tmp_path, "README.md", documented_call)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"search_genes": malformed_tool}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        main(["--root", str(tmp_path), "--catalog", str(catalog_path)])

    assert error.value.code == 2
    assert "catalog entry search_genes" in capsys.readouterr().err


def test_canonical_helper_matches_its_sha256_pin() -> None:
    root = Path(__file__).parents[2]
    helper = root / "docs/conformance/contract_truth.py"
    pin = root / "docs/conformance/contract_truth.sha256"

    assert pin.read_text(encoding="ascii") == f"{hashlib.sha256(helper.read_bytes()).hexdigest()}\n"
