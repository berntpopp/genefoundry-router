# syntax=docker/dockerfile:1

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder
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

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS prepared
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH" \
    GF_HOST=0.0.0.0 \
    GF_PORT=8000 \
    GF_MCP_PATH=/mcp \
    GF_HEALTHCHECK_HOST=localhost
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    # Strip the base image's system pip: the app runs entirely from the copied
    # /build/.venv (which contains no pip), so the interpreter-level pip is pure
    # attack surface. Removing it drops pip-only CVEs (e.g. CVE-2026-6357) that
    # the container-scan gate flags on a package the runtime never uses.
    && rm -rf /usr/local/lib/python3.14/ensurepip \
              /usr/local/lib/python3.14/site-packages/pip \
              /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
    # Persistent-store mount point for FASTMCP_HOME (OAuthProxy client/token store).
    # Owned by the app uid so a fresh named volume mounted here initializes writable
    # (Docker seeds an empty volume with the image dir's ownership). See prod compose.
    && mkdir -p /data && chown app:app /data
WORKDIR /home/app/web
COPY --from=builder /build/.venv /build/.venv
COPY servers.yaml pyproject.toml README.md ./
RUN /build/.venv/bin/python -c \
      "from importlib.resources import files; assert files('genefoundry_router.data').joinpath('fleet-baseline.json').is_file()" \
    # Bytecode is runtime cache, not release content. The final read-only image
    # must not carry interpreter caches inherited from the base or build steps.
    && find /usr/local/lib/python3.14 -type d -name __pycache__ -prune -exec rm -rf '{}' +
# Set-id bits are unnecessary under the non-root/no-new-privileges runtime
# contract and are rejected by the per-layer public-image policy.
RUN find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)"
# Debian currently ships one regular-file hardlink pair. Assert that exact
# inventory before removing the redundant alias so flattening cannot conceal
# a new or unexpected hardlink from the content gate.
RUN actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 | sort -k2,2)" \
    && { test "$actual" = "$expected" || { printf 'unexpected hardlinks:\n%s\n' "$actual"; exit 1; }; } \
    && rm -f /usr/bin/perl5.40.1

# Flatten the prepared filesystem into one release layer. The content gate still
# rejects set-id files and hardlinks in every layer, so a risky base-image layer
# cannot be hidden by a later chmod or deletion.
FROM scratch AS production
COPY --from=prepared / /

# Public release identity. Release automation supplies all three values from the
# validated source tag/commit and build record; the defaults identify an explicitly
# non-release local build. None of these arguments may contain secrets.
ARG APP_VERSION=0.0.0
ARG VCS_REF=0000000000000000000000000000000000000000
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="GeneFoundry Router" \
      org.opencontainers.image.description="FastMCP gateway for the GeneFoundry research MCP fleet" \
      org.opencontainers.image.source="https://github.com/berntpopp/genefoundry-router" \
      org.opencontainers.image.url="https://github.com/berntpopp/genefoundry-router" \
      org.opencontainers.image.documentation="https://github.com/berntpopp/genefoundry-router#container-release-and-deployment" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry" \
      org.genefoundry.research-use-only="true" \
      org.genefoundry.data-policy="code-only"
ENV VCS_REF="${VCS_REF}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH" \
    GF_HOST=0.0.0.0 \
    GF_PORT=8000 \
    GF_MCP_PATH=/mcp \
    GF_HEALTHCHECK_HOST=localhost
WORKDIR /home/app/web
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f -H "Host: ${GF_HEALTHCHECK_HOST}" http://localhost:8000/health || exit 1
CMD ["genefoundry-router", "run", "--host", "0.0.0.0", "--port", "8000"]
