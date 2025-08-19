output "service_url" {
  description = "Deployed Cloud Run service URL"
  value       = google_cloud_run_v2_service.app.uri
}
