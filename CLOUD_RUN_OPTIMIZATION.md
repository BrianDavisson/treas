# Cloud Run Performance Optimization Guide

## Key Optimizations Implemented

### 1. Application Level
- **Response Caching**: In-memory cache for template data (30min TTL)
- **Cloud Run Detection**: Optimized behavior when running in Cloud Run
- **Conditional Processing**: Skip expensive YTD generation during requests
- **Smart Startup**: Only regenerate if data is missing or very old (>1 hour)
- **Thread Pool**: Background processing with ThreadPoolExecutor

### 2. Container Optimizations  
- **Multi-stage Build**: Smaller production image
- **Health Checks**: Proper startup and liveness probes
- **Optimized Dependencies**: Minimal runtime packages
- **Memory Settings**: Increased to 1GB for better performance

### 3. Cloud Run Configuration
- **CPU Allocation**: 1 CPU with idle throttling enabled
- **Concurrency**: Limited to 10 requests per instance
- **Scaling**: 0 min instances (cost-effective), 10 max instances
- **Timeout**: 120 seconds for data processing
- **Probes**: Startup probe on /ready, liveness on /health

### 4. Gunicorn Settings
- **Workers**: Single worker to avoid memory issues
- **Threads**: 4 threads for I/O concurrency  
- **Preload**: App preloading for faster startup
- **Keep-alive**: 5 seconds to reuse connections

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISABLE_STARTUP_REGENERATE` | `0` | Set to `1` to skip startup data generation |
| `STARTUP_MONTH` | `auto` | Set to `YYYYMM` or `auto` for current month |
| `PORT` | `8080` | Container port (set by Cloud Run) |

## Performance Targets

- **Cold Start**: < 15 seconds to first request
- **Warm Response**: < 2 seconds for cached data
- **Memory Usage**: < 800MB under normal load
- **Cost**: Near-zero when idle (scales to 0)

## Monitoring Endpoints

- `GET /health` - Basic health check
- `GET /ready` - Readiness probe (returns 503 until startup complete)
- `GET /` - Main dashboard (cached responses)
- `GET /invest` - Investment calculator

## Deployment Commands

```bash
# Build optimized image
docker build -t treas-analyzer:optimized .

# Deploy to Cloud Run
gcloud run deploy treas-analyzer \
  --image gcr.io/PROJECT/treas-analyzer:optimized \
  --platform managed \
  --region us-west1 \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 10 \
  --timeout 120 \
  --min-instances 0 \
  --max-instances 10 \
  --allow-unauthenticated
```
