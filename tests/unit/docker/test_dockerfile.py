from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "docker" / "Dockerfile"


def test_dockerfile_exists_and_runs_router():
    text = DOCKERFILE.read_text()
    assert "FROM python:3.14-slim" in text
    assert "uv sync --frozen --no-dev" in text
    assert "EXPOSE 8000" in text
    # default command starts the router over http
    assert "genefoundry-router" in text
    assert "run" in text and "--host" in text and "0.0.0.0" in text  # noqa: S104 - asserting Dockerfile content
