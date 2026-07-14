from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

# The operator contract must be documented and REACHABLE from the README. Under README
# Standard v1 the detail lives in docs/ rather than above the fold, so assert both: the
# contract is written down, and the README actually links to the page it lives on.
OPERATOR_DOCS = (
    ROOT / "docs/configuration.md",
    ROOT / "docs/deployment.md",
)


def _documentation_set() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (README, *OPERATOR_DOCS))


def test_operator_docs_are_linked_from_the_readme() -> None:
    readme = README.read_text(encoding="utf-8")
    for doc in OPERATOR_DOCS:
        rel = doc.relative_to(ROOT).as_posix()
        assert doc.is_file(), f"{rel} is missing"
        assert f"]({rel})" in readme, f"README must link to {rel}"


def test_runtime_security_operator_contract_is_documented() -> None:
    docs = _documentation_set()
    for required in (
        "GF_ALLOWED_HOSTS",
        "GF_DRIFT_MODE",
        "GF_DRIFT_BASELINE",
        "changed startup",
        "additions/removals",
        "make snapshot-baseline",
        "Never re-pin",
        "code review",
    ):
        assert required in docs, f"operator contract lost in the refactor: {required!r}"


def test_production_example_sets_healthcheck_host() -> None:
    example = (ROOT / ".env.docker.example").read_text(encoding="utf-8")
    assert "GF_HEALTHCHECK_HOST=genefoundry.org" in example
