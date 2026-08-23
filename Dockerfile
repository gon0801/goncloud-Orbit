# Imagen de la app Orbit (API FastAPI + CLI de cron). Lockfile pinneado:
# `uv sync --frozen` falla si uv.lock no cubre pyproject.toml.
# Python 3.12 = requires-python del proyecto. El bind publico lo hace
# compose (127.0.0.1:8010:8000); 0.0.0.0 aqui es SOLO intra-contenedor
# (sin eso el port-map de Docker no alcanza uvicorn).
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
