# wedoc backend image — multi-stage uv build on Python 3.14
FROM ghcr.io/astral-sh/uv:0.9-python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer independent of source changes)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Install the project itself
COPY src ./src
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.14-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    BACKEND_STORAGE_LOCAL_PATH=/app/.assets/uploads

WORKDIR /app

# Runtime OS deps: libpq for asyncpg is statically bundled; only certs needed
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/.assets/uploads /app/.temporary

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts
COPY pyproject.toml uv.lock ./
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 3000

# The app runs its schema migration on ASGI lifespan startup (see main.lifespan),
# keeping the migration ledger byte-compatible with the reference deployment.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]
