# Commodity AI daily refresh job. On Cloud Run Jobs the shared bucket is mounted at DATA_MOUNT (deploy/gcp.sh):
# scripts/cloud_job.sh copies state in, runs the collectors and rebuild, and publishes a checked export.
FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12-slim AS builder
WORKDIR /app
ENV UV_LINK_MODE=copy
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY src ./src
RUN uv sync --frozen --no-dev --no-cache

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY --from=builder /app/.venv ./.venv
COPY src ./src
COPY scripts ./scripts
COPY config ./config
CMD ["/app/scripts/cloud_job.sh"]
