# K8s Dashboard UI - runs outside the cluster
# Build from the repo root: docker build -f dashboard/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/ .
COPY common ./common

EXPOSE 8080

CMD ["python", "app.py"]
