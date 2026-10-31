# K8s Dashboard UI - runs outside the cluster
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /data/kubepulse \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "--graceful-timeout", "30", "app:app"]
