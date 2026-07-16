"""Gates for the MCP Registry manifest and the fleet's CITATION.cff files.

Both are generated from fleet-metadata.yaml. The registry manifest carries a security
invariant worth a test of its own: only the router may be published as a reachable
`remotes` entry.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER_JSON = ROOT / "server.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("check_fleet_metadata")
gen_server = _load("gen_server_json")
gen_cff = _load("gen_citation_cff")


@pytest.fixture(scope="module")
def data() -> dict:
    return yaml.safe_load((ROOT / "fleet-metadata.yaml").read_text(encoding="utf-8"))


# --- MCP Registry manifest -----------------------------------------------------------


def test_server_json_is_current() -> None:
    """It is generated; a hand-edit or a version bump without regen must fail CI."""
    assert gen_server.main.__module__  # keep the import meaningful
    want = json.dumps(gen_server.render(), indent=2) + "\n"
    assert SERVER_JSON.read_text(encoding="utf-8") == want, "run `make server-json`"


def test_registry_description_fits_the_schema_cap() -> None:
    """The registry caps description at 100 chars — under half the About-box budget."""
    desc = gen_server.render()["description"]
    assert 0 < len(desc) <= gen_server.DESCRIPTION_MAX, f"{len(desc)} chars"


def test_registry_name_matches_the_schema_pattern(data: dict) -> None:
    assert gen_server.NAME_RE.match(data["registry"]["name"])


def test_registry_version_tracks_pyproject() -> None:
    pp = tomllib.loads((ROOT / "pyproject.toml").read_bytes().decode())
    assert gen_server.render()["version"] == pp["project"]["version"]


def test_only_streamable_http_is_offered() -> None:
    """The fleet does not offer SSE (AGENTS.md)."""
    remotes = gen_server.render()["remotes"]
    assert [r["type"] for r in remotes] == ["streamable-http"]


def test_no_backend_is_ever_published_as_a_remote(data: dict) -> None:
    """SECURITY INVARIANT.

    A registry `remotes` entry must be publicly reachable. The 21 backends are
    unauthenticated by design and reachable only behind the router (AGENTS.md), so
    publishing one as a remote would expose an unauthenticated server to the internet.
    The manifest must advertise the router's endpoint and nothing else.
    """
    manifest = gen_server.render()
    urls = [r["url"] for r in manifest["remotes"]]
    assert urls == [data["registry"]["endpoint"]]

    # No backend's namespace may appear as a host in any advertised remote.
    for ns in (b["namespace"] for b in data["backends"]):
        for url in urls:
            assert f"//{ns}-link." not in url, f"backend {ns} exposed as a registry remote"


# --- CITATION.cff --------------------------------------------------------------------


def test_citation_author_is_not_scraped_from_pyproject(data: dict) -> None:
    """The pyprojects disagree and are partly fabricated (pubtator's author is
    "AI Assistant"; gtex/litvar/stringdb use non-existent e-mail domains). A CITATION.cff
    exists to attribute a human, so the author is declared once, here."""
    authors = data["citation"]["authors"]
    assert authors, "no citation author declared"
    for a in authors:
        assert a.get("family-names") and a.get("given-names")
        assert "@" in a.get("email", "")
        assert "AI Assistant" not in f"{a['given-names']} {a['family-names']}"


def test_every_fleet_repo_gets_a_citation_entry(data: dict) -> None:
    slugs = [slug for slug, _ in gen_cff.repo_slugs()]
    assert len(slugs) == len(data["backends"]) + 1
    assert "genefoundry-router" in slugs
    assert len(set(slugs)) == len(slugs)


def test_citation_fleet_dir_escapes_a_named_worktree(tmp_path: Path) -> None:
    """Fleet-level generation must locate siblings from an isolated worktree."""
    fleet = tmp_path / "development"
    router = fleet / "genefoundry-router"
    router.mkdir(parents=True)
    (router / "pyproject.toml").write_text('[project]\nname = "genefoundry-router"\n')
    worktree = router / ".worktrees" / "release-candidate"
    worktree.mkdir(parents=True)

    assert gen_cff.fleet_dir(worktree) == fleet


def test_rendered_citation_has_the_fields_github_needs(data: dict) -> None:
    """GitHub renders "Cite this repository" from these; a missing one silently
    degrades the citation rather than failing loudly."""
    slug, entry = gen_cff.repo_slugs()[0]
    cff = yaml.safe_load(gen_cff.render(slug, entry, data, "2026-07-14"))
    for field in (
        "cff-version",
        "message",
        "title",
        "abstract",
        "type",
        "authors",
        "version",
        "license",
        "repository-code",
    ):
        assert cff.get(field), f"CITATION.cff is missing {field!r}"
    assert cff["cff-version"] == "1.2.0"
    assert cff["type"] == "software"
    assert cff["license"] == "MIT"
