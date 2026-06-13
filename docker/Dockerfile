# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY genefoundry_router ./genefoundry_router
# --no-editable: install the project INTO site-packages (not as an editable .pth that
# would reference the builder path), so the copied .venv is self-contained in the
# production stage. A plain editable install breaks across multi-stage COPY.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH" \
    GF_HOST=0.0.0.0 \
    GF_PORT=8000 \
    GF_MCP_PATH=/mcp
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /home/app/web
COPY --from=builder /build/.venv /build/.venv
COPY genefoundry_router ./genefoundry_router
COPY servers.yaml pyproject.toml README.md ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["genefoundry-router", "run", "--host", "0.0.0.0", "--port", "8000"]
