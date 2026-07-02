FROM python:3.13-slim

WORKDIR /app
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV CORTEX_ROOT=/app

CMD ["python", "-m", "cortex.web"]
