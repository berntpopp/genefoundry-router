"""#336: scripts/check_skills.py gates skill frontmatter, size, tool names and shared rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_skills import ROOT, canonical_block, check_skills

BLOCK = canonical_block(ROOT / "skills" / "README.md")
CATALOG = {"gnomad_get_variant_frequencies", "hgnc_resolve_symbol"}


def _skill(
    root: Path,
    folder: str = "demo-skill",
    *,
    name: str | None = None,
    description: str = "Use when demonstrating the linter.",
    body: str = "Call `gnomad_get_variant_frequencies` then `call_tool` and `search_tools`.",
    block: str = BLOCK,
) -> Path:
    (root / "README.md").write_text(
        f"x\n<!-- shared-rules:start -->\n{BLOCK}\n<!-- shared-rules:end -->\n"
    )
    path = root / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name or folder}\ndescription: {description}\n---\n\n{body}\n\n{block}\n"
    )
    return path


def test_valid_skill_passes(tmp_path: Path) -> None:
    _skill(tmp_path)
    assert check_skills(tmp_path, CATALOG) == []


@pytest.mark.parametrize(
    ("folder", "name"), [("demo-skill", "other-skill"), ("Bad_Name", "Bad_Name")]
)
def test_bad_name_or_folder_mismatch(tmp_path: Path, folder: str, name: str) -> None:
    _skill(tmp_path, folder, name=name)
    errors = check_skills(tmp_path, CATALOG)
    assert errors and all(folder in e for e in errors)


@pytest.mark.parametrize("description", ["Helps with things.", "Use when " + "x" * 1020])
def test_bad_description(tmp_path: Path, description: str) -> None:
    _skill(tmp_path, description=description)
    assert any("description" in e for e in check_skills(tmp_path, CATALOG))


def test_oversized_skill(tmp_path: Path) -> None:
    _skill(tmp_path, body="x" * 20_000)
    assert any("20000" in e for e in check_skills(tmp_path, CATALOG))


def test_unknown_tool_in_known_namespace(tmp_path: Path) -> None:
    _skill(tmp_path, body="Call `gnomad_get_frequency` and `notabackend_foo`.")
    [error] = check_skills(tmp_path, CATALOG)
    assert "demo-skill" in error and "gnomad_get_frequency" in error


def test_unknown_tool_in_references(tmp_path: Path) -> None:
    _skill(tmp_path)
    ref = tmp_path / "demo-skill" / "references" / "pvs1.md"
    ref.parent.mkdir()
    ref.write_text("Use `hgnc_resolve_symbols(...)` here.")
    [error] = check_skills(tmp_path, CATALOG)
    assert "references/pvs1.md" in error and "hgnc_resolve_symbols" in error


@pytest.mark.parametrize("block", ["", BLOCK.replace("Research use only", "Research use")])
def test_missing_or_altered_shared_block(tmp_path: Path, block: str) -> None:
    _skill(tmp_path, block=block)
    [error] = check_skills(tmp_path, CATALOG)
    assert "shared rules" in error


def test_repo_skills_pass() -> None:
    from scripts.check_skills import catalog_names

    errors = check_skills(ROOT / "skills", catalog_names())
    somatic = [e for e in errors if "somatic-variant-interpretation" in e]
    assert somatic == []  # other skills are gated by `make lint-skills`


def test_folder_without_skill_md_fails(tmp_path: Path) -> None:
    _skill(tmp_path)
    (tmp_path / "typo-skill").mkdir()
    (tmp_path / "typo-skill" / "Skill.md").write_text("x")
    assert any("typo-skill: no SKILL.md" in e for e in check_skills(tmp_path, CATALOG))


def test_broken_reference_link_fails(tmp_path: Path) -> None:
    path = _skill(tmp_path, body="Read `references/pvs1.md` and [x](references/gone.md).")
    (path.parent / "references").mkdir()
    (path.parent / "references" / "pvs1.md").write_text("ok")
    errors = check_skills(tmp_path, CATALOG)
    assert errors == ["demo-skill: broken link `references/gone.md` in demo-skill/SKILL.md"]


def test_tool_calls_block_generated_and_checked(tmp_path: Path) -> None:
    entries = {
        "gnomad_get_variant_frequencies": {
            "description": "x. Signature: get_variant_frequencies(variant_id, dataset=).",
            "params": {"variant_id": "", "dataset": ""},
        }
    }
    marker = "<!-- tool-calls:start -->\n<!-- tool-calls:end -->"
    path = _skill(tmp_path, body=f"Use `gnomad_get_variant_frequencies`.\n\n{marker}")
    names = set(entries)
    assert check_skills(tmp_path, names, entries, set()) == [
        "demo-skill: tool-calls block is stale (run check_skills.py --write)"
    ]
    assert check_skills(tmp_path, names, entries, set(), write=True) == []
    assert (
        "- `gnomad_get_variant_frequencies`(variant_id; optional: dataset) — via call_tool"
        in path.read_text()
    )
    assert check_skills(tmp_path, names, entries, set()) == []
