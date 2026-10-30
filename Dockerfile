# Alley Oop Analytics: one image used by both the web and ingest services.
# Builds natively on arm64 (Raspberry Pi 4/5, Apple Silicon) and amd64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/alleyoop.db

WORKDIR /app

COPY requirements.txt .
# Runtime deps only (pytest/httpx are dev-only)
RUN grep -vE '^(pytest|httpx)' requirements.txt > /tmp/req.txt && pip install -r /tmp/req.txt

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app:app /app/data
COPY --chown=app:app app ./app
COPY --chown=app:app ingest ./ingest

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
