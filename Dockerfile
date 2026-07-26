FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AFLOW_OPERATIONS_ROOT=/var/lib/aflow/operations \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

VOLUME ["/var/lib/aflow/operations", "/workspace"]

ENTRYPOINT ["aflow-cli"]