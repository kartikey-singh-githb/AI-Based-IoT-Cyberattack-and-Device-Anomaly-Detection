FROM python:3.12-slim

WORKDIR /app

# Runtime dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root user; the app writes only to /data and its artifact volume.
RUN useradd --create-home --uid 10001 p310 \
    && mkdir -p /data /app/logs \
    && chown -R p310:p310 /data /app/logs /app/app/ml/artifacts
USER p310

ENV P310_HOST=0.0.0.0 \
    P310_PORT=8000 \
    P310_DATABASE_URL=sqlite:////data/p310.db \
    P310_LOG_FILE=/app/logs/backend.log

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${P310_PORT}"]
