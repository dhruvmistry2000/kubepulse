# K8s Dashboard UI - runs outside the cluster
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

# Cloud Run uses PORT env (default 8080); app reads it at runtime
EXPOSE 8080

CMD ["python", "app.py"]
