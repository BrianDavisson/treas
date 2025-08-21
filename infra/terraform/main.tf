terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.31.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable needed APIs
resource "google_project_service" "services" {
  for_each           = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# Artifact Registry Docker repo
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = var.repo_name
  description   = "Container images for treas analyzer"
  format        = "DOCKER"
  depends_on    = [google_project_service.services]
}

# Service account for Cloud Run
resource "google_service_account" "runner" {
  account_id   = var.service_account_name
  display_name = "Cloud Run runtime SA for treas-analyzer"
}

# Allow Cloud Run to pull from Artifact Registry
resource "google_project_iam_member" "ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

# Cloud Run service with optimized settings
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runner.email
    
    # Optimize for faster cold starts
    max_instance_request_concurrency = 10
    timeout                          = "120s"
    
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}/${var.image_name}:${var.image_tag}"
      ports { 
        container_port = 8080 
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"  # Increased for better performance
        }
        cpu_idle = true   # Allow CPU throttling when idle
      }
      
      # Environment variables for Cloud Run optimization
      env {
        name  = "PORT"
        value = "8080"
      }
      env {
        name  = "DISABLE_STARTUP_REGENERATE"
        value = "0"  # Enable optimized startup
      }
      env {
        name  = "STARTUP_MONTH"
        value = "auto"
      }
      
      # Health check configuration
      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 30
        period_seconds       = 60
        timeout_seconds      = 10
        failure_threshold    = 3
      }
      
      startup_probe {
        http_get {
          path = "/ready"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds       = 10
        timeout_seconds      = 5
        failure_threshold    = 30
      }
    }
    
    # Scaling configuration for better performance
    scaling {
      min_instance_count = 0  # Allow scaling to zero
      max_instance_count = 10 # Reasonable upper limit
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image
    ]
  }

  depends_on = [
    google_artifact_registry_repository.repo,
    google_project_service.services,
  ]
}

# Public (unauthenticated) access
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
