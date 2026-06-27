FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY workforce_runtime ./workforce_runtime
COPY examples ./examples
COPY docs ./docs
COPY docker/workforce_runtime_config.docker.json ./workforce_runtime_config.json
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=8 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"

CMD ["/usr/local/bin/entrypoint.sh"]
