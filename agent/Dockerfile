# K8s Dashboard Agent - runs inside the cluster
# Build from the repo root: docker build -f agent/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ .
COPY common ./common

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "--graceful-timeout", "30", "app:app"]
