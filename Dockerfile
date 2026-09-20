# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM python:3.12.11-slim-bookworm AS runtime

COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/backend:/app"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY backend ./backend
COPY mcp_server ./mcp_server
COPY data ./data
COPY README.md ./

RUN groupadd --system --gid 10001 fixflow \
    && useradd --system --uid 10001 --gid fixflow --home-dir /app fixflow \
    && chown -R fixflow:fixflow /app

USER 10001:10001

CMD ["python", "-m", "app.api.run"]
