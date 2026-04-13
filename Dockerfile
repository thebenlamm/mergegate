# MergeGate API server image
# Uses CMD (not ENTRYPOINT) so `docker compose run` can override for migrations/seeding.

FROM python:3.12-slim AS base

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Layer cache: deps first, code second
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY api/ api/
COPY scripts/ scripts/
COPY db/ db/
COPY tasks/ tasks/
COPY alembic.ini ./

# Expose API port
EXPOSE 8000

# CMD allows override for migrations: docker compose run --rm api alembic upgrade head
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
