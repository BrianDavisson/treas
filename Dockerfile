# Python slim base
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for scientific stack
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY treas_analyzer ./treas_analyzer
COPY webapp ./webapp
COPY out ./out

# Port for Cloud Run
ENV PORT=8080
EXPOSE 8080

# Gunicorn entrypoint for Flask app
CMD ["gunicorn", "webapp.app:app", "-b", ":8080", "--workers", "2", "--threads", "4", "--timeout", "120"]
