# Task 6 (tasks/todo.md, Phase 1): Cloud Run service + its Task-2b/Task-3 wiring.
#
# ingress = INGRESS_TRAFFIC_INTERNAL_ONLY, using the default run.app URL (not a
# custom domain): confirmed against Google's Cloud Run ingress documentation that
# Pub/Sub push subscriptions in the same project/VPC-SC perimeter are treated as
# internal traffic under this setting, using that exact combination -- this is what
# lets the service stay off the public internet while still being reachable by
# Pub/Sub's push mechanism.

resource "google_cloud_run_v2_service" "this" {
  project  = google_project.this.project_id
  name     = "email-triage-${var.client_name}"
  location = var.region

  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.cloud_run_runtime.email

    # Task 2b's connector, finally wired up: every outbound call this service makes
    # routes through the no-public-egress VPC network.
    vpc_access {
      connector = google_vpc_access_connector.this.id
      egress    = "ALL_TRAFFIC"
    }

    containers {
      # Placeholder image -- the resource needs *some* image to be created at all.
      # Task 22's `gcloud run deploy --source .` publishes the real one; the
      # lifecycle block below stops a later `terraform apply` from reverting that
      # deploy back to this placeholder.
      image = var.container_image

      env {
        name  = "CLOUD_RUN_AUDIENCE"
        value = local.push_oidc_audience
      }
      env {
        name  = "INVOKER_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.invoker.email
      }
      env {
        name  = "CONFIDENCE_THRESHOLD"
        value = "0.75" # placeholder default -- SPEC-email-triage-core.md Open Questions
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_iam_member.runtime_vertex_ai_user]
}

# Scoped to this one service, not project-wide -- deferred from Task 3 to here, now
# that the service exists to scope it to.
resource "google_cloud_run_v2_service_iam_member" "invoker_can_invoke" {
  project  = google_project.this.project_id
  location = google_cloud_run_v2_service.this.location
  name     = google_cloud_run_v2_service.this.name

  role   = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.invoker.email}"
}
