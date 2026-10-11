# Multi-stage build. The runtime image carries no build toolchain.
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so a source change does not invalidate the layer.
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GEO_CACHE_DIR=/var/cache/mcp-geospatial

# Run unprivileged. The cache directory is the only writable path needed.
RUN useradd --create-home --uid 10001 geo \
    && mkdir -p /var/cache/mcp-geospatial \
    && chown -R geo:geo /var/cache/mcp-geospatial

WORKDIR /app
COPY --from=builder --chown=geo:geo /app/.venv /app/.venv
COPY --from=builder --chown=geo:geo /app/src /app/src

USER geo

# stdio by default, so `docker run -i` works with any MCP client.
# For HTTP:  docker run -p 8000:8000 <image> --transport http
ENTRYPOINT ["mcp-geospatial"]
