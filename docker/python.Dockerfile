# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.4 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
# Preserve the editable model dependency's relative path from pyproject.toml.
COPY model/pyproject.toml model/README.md /app/model/
COPY backend/pyproject.toml backend/uv.lock ./
# Cache heavy dependencies independently of application source changes.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-package loadpilot-model
COPY model/loadpilot_model /app/model/loadpilot_model
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY backend/ ./
COPY docker/healthcheck.py /app/docker/healthcheck.py
EXPOSE 8000 9000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
