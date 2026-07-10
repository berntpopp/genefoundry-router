from pathlib import Path


def test_runtime_security_operator_contract_is_documented() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    for required in (
        "GF_ALLOWED_HOSTS",
        "GF_DRIFT_MODE",
        "GF_DRIFT_BASELINE",
        "changed startup failure",
        "additions/removals",
        "make snapshot-baseline",
        "never re-pin",
        "code review",
    ):
        assert required in readme


def test_production_example_sets_healthcheck_host() -> None:
    example = Path(".env.docker.example").read_text(encoding="utf-8")
    assert "GF_HEALTHCHECK_HOST=genefoundry.org" in example
