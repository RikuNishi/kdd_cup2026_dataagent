FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY configs/react_baseline.example.yaml ./configs/react_baseline.example.yaml
COPY src ./src
COPY docs ./docs

RUN uv sync --no-dev --frozen


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/configs ./configs
COPY --from=builder /app/src ./src
COPY --from=builder /app/docs ./docs
COPY --from=builder /app/README.md ./README.md

ENTRYPOINT ["/app/.venv/bin/dabench", "submit-run"]
