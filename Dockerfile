FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --no-log-init --create-home --uid 10001 app

COPY pyproject.toml README.md ./
COPY app/ ./app/
RUN python -m pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY docker-entrypoint.sh ./

USER app

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
