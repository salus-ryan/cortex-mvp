FROM python:3.13-slim

WORKDIR /app
COPY . /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm ca-certificates \
    && npm install -g @railway/cli@5.23.3 \
    && pip install --no-cache-dir 'pytest>=7.0.0' \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV CORTEX_ROOT=/app

ENTRYPOINT ["python", "-m", "cortex.pid1"]
