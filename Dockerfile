FROM python:3.13-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir pytest>=7.0.0

ENV PYTHONUNBUFFERED=1
ENV CORTEX_ROOT=/app

ENTRYPOINT ["python", "-m", "cortex.pid1"]
