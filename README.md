# Treasury Yield Analyzer

Fetches the U.S. Treasury Daily Yield Curve XML for the current month (YYYYMM), plots yields by maturity, and summarizes which maturity looks like the best value based on current yield and short-term trend.

## What it does
- Dynamically builds the XML URL for the current year and month
- Parses daily yields across maturities (1M … 30Y)
- Produces plots:
  - All maturities on one chart
  - Year-to-date (YTD) across months (up to 12 months)
  - Small multiples, one chart per maturity
- Computes a simple trend for each maturity (linear slope and R²)
- Prints a short summary and saves it to `out/summary_YYYYMM.txt`
 - Caches network fetches and images; by default regenerates once per day after 12:00 ET

## Quick start (CLI)

1) Create a virtual environment and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Run

```bash
python -m treas_analyzer
```

Optional args:

```bash
python -m treas_analyzer --month 202508     # specific month (YYYYMM)
python -m treas_analyzer --show             # also open plots interactively
python -m treas_analyzer --insecure         # disable SSL verify (only if behind proxy)
python -m treas_analyzer --force-regenerate # ignore ET cache window and rebuild now
python -m treas_analyzer --out ./out        # custom output directory (defaults to ./out)
```

Outputs are written to the `out/` folder: plots and a text summary.

Cache behavior (ET):
- If outputs are missing, they are generated.
- If already generated today, they are reused.
- If not yet generated today and current ET time is >= 12:00, fresh outputs are generated.
- Use `--force-regenerate` to bypass the cache at any time.

## Web app

Run a local Flask site that renders the same plots and summary:

```bash
export FLASK_APP=webapp.app:app
python -m flask run -p 8080
# open http://localhost:8080
```

Or with gunicorn:

```bash
gunicorn webapp.app:app -b :8080
```

Query params:
- `?month=YYYYMM` to view a specific month
- `?insecure=1` to bypass SSL verification if needed (proxy env)

Notes:
- The page attempts to reuse cached outputs using the same ET-based logic. It will display up to three images in order: All, YTD, Facets. If the YTD image is missing (e.g., due to earlier network issues), pre-warm by running the CLI with `--force-regenerate`.
- Double-click/tap a plot to toggle fullscreen (if supported by your browser/device).
- Health probe: `GET /healthz` returns a simple JSON.

## Container (GCP Cloud Run)

Build and run locally:

```bash
docker build -t treas-analyzer:latest .
docker run -p 8080:8080 treas-analyzer:latest
```

Deploy to Cloud Run using Artifact Registry (example):

```bash
# Auth and project
# gcloud auth login
# gcloud config set project YOUR_PROJECT

REGION=us-central1
IMAGE=treas-a
REPO=treas-a
PROJECT=my-project


# Enable docker auth for Artifact Registry
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build and push
docker build -t ${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest .
docker push ${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest

# Deploy
gcloud run deploy ${IMAGE} \
  --image ${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --port 8080
```

Notes:
- If your environment requires outbound proxy CA certs, either bake them into the image and set `REQUESTS_CA_BUNDLE` or use `?insecure=1` only if acceptable.
- The app generates plots into `/app/out` at runtime; Cloud Run uses ephemeral storage, which is fine for per-request rendering.

Terraform deployment:
- This repo includes Terraform to provision Artifact Registry and a public Cloud Run service. See `infra/terraform/README.md` for end-to-end steps (enable APIs, create repo, deploy service, and output the service URL).

## Notes
- Data source: U.S. Treasury Daily Treasury Yield Curve Rates (XML view).
- Network access is required. If the website is unreachable or the XML changes, the parser will attempt a fallback strategy.
- “Best value” is heuristic: it blends current yield, trend slope (penalizing rising yields), and trend confidence (R²). See code comments for details.
