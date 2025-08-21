# Multi-stage build for optimized Cloud Run deployment
FROM python:3.12-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISABLE_STARTUP_REGENERATE=0 \
    STARTUP_MONTH=auto

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    libjpeg62-turbo \
    libpng16-16 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY treas_analyzer ./treas_analyzer
COPY webapp ./webapp

# Create out directory for generated files
RUN mkdir -p out

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Port for Cloud Run
ENV PORT=8080
EXPOSE 8080

# Health check for container readiness
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/ready || exit 1

# Optimized gunicorn settings for Cloud Run
# Single worker to avoid memory issues, threads for concurrency
CMD ["gunicorn", "webapp.app:app", "-b", ":8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--keep-alive", "5", "--preload", "--access-logfile", "-", "--error-logfile", "-"]
