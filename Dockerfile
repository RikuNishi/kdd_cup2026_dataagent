FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY configs/react_baseline.example.yaml ./configs/react_baseline.example.yaml
COPY src ./src
COPY docs ./docs

RUN uv sync --no-dev --frozen

ENTRYPOINT ["uv", "run", "dabench", "submit-run"]
