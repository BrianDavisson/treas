# Terraform for GCP Cloud Run (treas-analyzer)

This Terraform provisions:
- Required APIs
- Artifact Registry Docker repo
- Service account and IAM bindings
- Cloud Run v2 service (public)

## Prereqs
- gcloud CLI installed and authenticated
- Terraform >= 1.5
- Project: `gcloud-davisson-project`
- Container image pushed to `REGION-docker.pkg.dev/gcloud-davisson-project/treas-analyzer/treas-analyzer:latest`

## Build and push image (example)

```bash
# from repo root
PROJECT=gcloud-davisson-project
REGION=us-west1
IMAGE=treas-analyzer
TAG=latest

gcloud auth configure-docker ${REGION}-docker.pkg.dev

docker build -t ${REGION}-docker.pkg.dev/${PROJECT}/treas-analyzer/${IMAGE}:${TAG} .
docker push ${REGION}-docker.pkg.dev/${PROJECT}/treas-analyzer/${IMAGE}:${TAG}
```

## Deploy

```bash
cd infra/terraform
terraform init
terraform apply \
  -var project_id=cloud-davisson-project \
  -var region=us-west1 \
  -var repo_name=treas-analyzer \
  -var image_name=treas-analyzer \
  -var image_tag=latest
```

Outputs will include the `service_url`.
