# ODO — One Door Orchestrator
# Multi-stage build: compile C extensions in builder, slim runtime.

# --- Builder stage: install deps that need compilation ---
FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

# --- Runtime stage ---
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy source
COPY odo/         odo/
COPY engram/      engram/
COPY search/      search/
COPY quality/     quality/
COPY knowledge/   knowledge/
COPY souls/       /data/soul/
COPY think_router.py .

# Environment
ENV ODO_PORT=8084
ENV ODO_BACKEND=http://inference:8081
ENV CHIMERE_HOME=/data
ENV SOUL_DIR=/data/soul
ENV PYTHONUNBUFFERED=1

EXPOSE 8084

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8084/health || exit 1

CMD ["python", "odo/odo.py"]
