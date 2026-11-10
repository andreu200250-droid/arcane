FROM python:3.12-slim

# curl обязателен: через него идут запросы к Steam (urllib Steam режет по TLS-отпечатку)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py index.html ./

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SCAN_INTERVAL=900 \
    DB_PATH=/data/monitor.db

RUN mkdir -p /data \
    && useradd -r -u 10001 -m monitor \
    && chown -R monitor:monitor /app /data
USER monitor

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python3", "app.py"]
