# Task 1 (tasks/todo.md, Phase 0): project bootstrap + required API enablement.
# Scoped to exactly one client per SPEC-email-triage-core.md — never reused across
# clients. VPC-SC perimeter (Task 2), IAM (Task 3), Pub/Sub (Task 4), and BigQuery
# (Task 5) are deliberately left to their own files, added in later tasks.

resource "google_project" "this" {
  name       = "email-triage-${var.client_name}"
  project_id = var.project_id
  org_id     = var.folder_id == null ? var.org_id : null
  folder_id  = var.folder_id

  billing_account = var.billing_account

  labels = {
    client     = var.client_name
    app        = "email-triage-core"
    managed_by = "terraform"
  }
}

locals {
  # Every API this module's pipeline calls at runtime, plus the two platform
  # services (IAM, Access Context Manager) needed to secure it. Keep this list in
  # sync with the "Network isolation" egress list in SPEC-email-triage-core.md --
  # anything enabled here that isn't also allowed through the VPC-SC perimeter
  # (Task 2) is a service the app can reach but shouldn't need to.
  required_apis = [
    "gmail.googleapis.com",
    "aiplatform.googleapis.com", # Vertex AI
    "pubsub.googleapis.com",
    "run.googleapis.com", # Cloud Run
    "bigquery.googleapis.com",
    "firestore.googleapis.com", # historyId cursor state store only (Task 9) -- not the deferred PHI content store
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudbuild.googleapis.com", # Cloud Run container builds/deploys
    "iam.googleapis.com",
    "accesscontextmanager.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project = google_project.this.project_id
  service = each.value

  # Don't let `terraform destroy` silently disable a live client's APIs -- that's
  # an outage, not a cleanup. Sandbox/dev teardown should disable explicitly if
  # ever needed, not rely on this default.
  disable_on_destroy = false
}
