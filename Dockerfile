# ─────────────────────────────────────────────────────────────
# Dockerfile — Fingerspot ADMS Real-Time Middleware
# ─────────────────────────────────────────────────────────────

FROM python:3.11-slim AS builder

WORKDIR /build

# Install compiler dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install curl untuk healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages dari builder
COPY --from=builder /install /usr/local

# Copy source code & init scripts
COPY app/ ./app/

# Buat non-root user untuk keamanan
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser
USER appuser

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=5005

EXPOSE 5005
EXPOSE 8000

# Health check bawaan Docker
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5005/health || exit 1

# Jalankan dengan uvicorn
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "5005", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log"]
