variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region (e.g., us-west1)"
  type        = string
  default     = "us-west1"
}

variable "repo_name" {
  description = "Artifact Registry Docker repository name"
  type        = string
  default     = "treas-analyzer"
}

variable "service_account_name" {
  description = "Service account ID (without domain)"
  type        = string
  default     = "treas-analyzer-sa"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "treas-analyzer"
}

variable "image_name" {
  description = "Container image name"
  type        = string
  default     = "treas-analyzer"
}

variable "image_tag" {
  description = "Container image tag"
  type        = string
  default     = "latest"
}
