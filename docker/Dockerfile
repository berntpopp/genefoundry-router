# syntax=docker/dockerfile:1

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY genefoundry_router ./genefoundry_router
# --no-editable: install the project INTO site-packages (not as an editable .pth that
# would reference the builder path), so the copied .venv is self-contained in the
# production stage. A plain editable install breaks across multi-stage COPY.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH" \
    GF_HOST=0.0.0.0 \
    GF_PORT=8000 \
    GF_MCP_PATH=/mcp
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    # Strip the base image's system pip: the app runs entirely from the copied
    # /build/.venv (which contains no pip), so the interpreter-level pip is pure
    # attack surface. Removing it drops pip-only CVEs (e.g. CVE-2026-6357) that
    # the container-scan gate flags on a package the runtime never uses.
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12
WORKDIR /home/app/web
COPY --from=builder /build/.venv /build/.venv
COPY genefoundry_router ./genefoundry_router
COPY servers.yaml pyproject.toml README.md ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["genefoundry-router", "run", "--host", "0.0.0.0", "--port", "8000"]
