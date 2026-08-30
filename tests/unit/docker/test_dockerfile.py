from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "docker" / "Dockerfile"
PYTHON_314_INDEX = (
    "python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5"
)


def test_dockerfile_exists_and_runs_router():
    text = DOCKERFILE.read_text()
    assert "FROM python:3.14-slim" in text
    assert "uv sync --frozen --no-dev" in text
    assert "EXPOSE 8000" in text
    # default command starts the router over http
    assert "genefoundry-router" in text
    assert "run" in text and "--host" in text and "0.0.0.0" in text  # noqa: S104 - asserting Dockerfile content


def test_dockerfile_uses_reviewed_python_314_index_in_both_stages() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert text.count(f"FROM {PYTHON_314_INDEX}") == 2


def test_dockerfile_applies_available_runtime_security_upgrades() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    prepared = text.split(f"FROM {PYTHON_314_INDEX} AS prepared", maxsplit=1)[1].split(
        "FROM scratch AS production", maxsplit=1
    )[0]

    assert "apt-get update && apt-get upgrade -y" in prepared
