# ODO — One Door Orchestrator
# Multi-stage build: slim runtime with only what the proxy needs.

FROM python:3.12-slim AS base

# System deps: build-essential for faiss-cpu C extensions, curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY odo/         odo/
COPY engram/      engram/
COPY search/      search/
COPY quality/     quality/
COPY knowledge/   knowledge/
COPY think_router.py .

# Default SOUL location inside container
RUN mkdir -p /data/soul/default

# Environment
ENV ODO_PORT=8084
ENV ODO_BACKEND=http://inference:8081
ENV CHIMERE_HOME=/data
ENV SOUL_DIR=/data/soul
ENV PYTHONUNBUFFERED=1

EXPOSE 8084

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8084/stats || exit 1

CMD ["python", "odo/odo.py"]
