# Commodity AI daily refresh job. On Cloud Run Jobs the shared bucket is mounted at DATA_MOUNT (deploy/gcp.sh):
# scripts/cloud_job.sh copies state in, runs the collectors and rebuild, and publishes a checked export.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY scripts ./scripts
COPY config ./config
CMD ["/app/scripts/cloud_job.sh"]
