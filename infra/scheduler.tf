# Task 20 (tasks/todo.md, Phase 6): Cloud Scheduler-triggered watch renewal.
#
# Confirmed against Google's Cloud Run ingress docs (same source already used for
# Pub/Sub in cloud_run.tf) that Cloud Scheduler, like Pub/Sub, is recognized as
# internal traffic under INGRESS_TRAFFIC_INTERNAL_ONLY when calling the default
# run.app URL in the same project -- no ingress exception needed for this job.

resource "google_cloud_scheduler_job" "renew_watch" {
  project = google_project.this.project_id
  region  = var.region
  name    = "email-triage-${var.client_name}-renew-watch"

  # Daily, well inside Gmail's ~7-day watch expiry window -- plenty of margin
  # even if a given day's attempt fails and Scheduler's own retry_config also
  # doesn't succeed before the next scheduled run.
  schedule  = "0 3 * * *"
  time_zone = "UTC"

  http_target {
    uri         = "${google_cloud_run_v2_service.this.uri}/renew-watch"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.invoker.email
      # Same fixed audience as the Pub/Sub push subscription (infra/main.tf) --
      # _verify_push_request in src/main.py checks against this one value for
      # both routes.
      audience = local.push_oidc_audience
    }
  }

  retry_config {
    retry_count = 3
  }
}
