# Small, script-facing outputs -- lets infra/deploy.sh (Task 22) and future
# CI tooling consume project_id/region/service name without re-parsing
# terraform.tfvars by hand.

output "project_id" {
  description = "The client's dedicated GCP project ID."
  value       = google_project.this.project_id
}

output "region" {
  value = var.region
}

output "cloud_run_service_name" {
  value = google_cloud_run_v2_service.this.name
}

output "cloud_run_url" {
  description = "The deployed service's default run.app URL."
  value       = google_cloud_run_v2_service.this.uri
}
